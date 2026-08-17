"""
dataset_builder — 训练数据集自动生成（交易机侧）

## 目的
从历史交易数据（DecisionSnapshot / DecisionRetrospective）构造 SFT 监督微调
数据集，用于在内网 GPU 机上微调 Qwen3-30B-A3B，使其学会
「在什么市场统计状态下、用什么门控参数最优」。

## 标签策略（务实派，不依赖重型回测网格搜索）
对每个 (regime, 时间窗口) 聚合统计，根据该窗口实际交易结果构造监督标签：
  - 净盈利窗口：标签 = 当前参数（正向强化，「这组参数在此状态管用」）
  - 净亏损窗口：标签 = 纠正后的更保守参数（「此状态应更收紧」）
  - 手续费侵蚀严重：标签 = 收紧日交易上限 + 提高盈亏比
纠正幅度参考 decision_feedback_service 的 4 条规则逻辑（保持一致性）。

这样产出的 (市场统计 → 建议参数) 映射是合法的 SFT 监督信号，
避免了网格搜索回测的高复杂度，同时仍能从结果中学习。
未来数据充足时可升级为回测算最优标签（见 _label_v2 预留接口）。

## 输出
标准 JSONL，每行一个 messages 对象（兼容 HuggingFace datasets / Unsloth）。
产出文件发送给 GPU 机的 training/data/ 目录。

用法：
    python -m backend.services.local_llm.dataset_builder --days 90 --out training/data/train.jsonl
    python -m backend.services.local_llm.dataset_builder --dry-run  # 只统计不写文件

详见 docs/LOCAL_LLM_TRADING_HOST_GUIDE.md 第 3 节。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

# 本模型可建议的门控参数（与 gate_optimizer_service._OPTIMIZABLE_KEYS 对齐）。
_OPTIMIZABLE_KEYS = ("min_risk_reward", "scalp_min_confidence", "max_daily_trades")

# 参数基准值（runtime_tuning_store._DEFAULT_SCHEMA 的 value），用于标签构造。
_BASELINE_PARAMS: Dict[str, float] = {
    "min_risk_reward": 1.8,
    "scalp_min_confidence": 70,
    "max_daily_trades": 12,
}

# 参数边界（与 gate_optimizer_service._PARAM_BOUNDS 一致）。
_PARAM_BOUNDS: Dict[str, Tuple[float, float]] = {
    "min_risk_reward": (1.5, 3.0),
    "scalp_min_confidence": (55.0, 85.0),
    "max_daily_trades": (3.0, 10.0),
}

# 聚合窗口大小（天）。每个窗口产出一条训练样本。
_WINDOW_DAYS = 7

# 窗口滑动步长（天）。步长 < 窗口大小 = 滑窗扩增数据量。
_STEP_DAYS = 3

# 每窗口最少样本数，不足则跳过（避免噪声标签）。
_MIN_TRADES_PER_WINDOW = 8

_SYSTEM_PROMPT = (
    "你是加密永续合约交易平台的门控参数优化器。根据给定的市场状态和历史交易统计，"
    "输出建议的门控参数。决策原则：手续费占比高则降频收紧，平均亏损>平均盈利则"
    "提高盈亏比门槛，胜率高且赚多亏少则可适度放松。只能输出 JSON，不要输出任何其它内容。"
)


# ---------------------------------------------------------------------------
# 数据采集：按时间窗口聚合 DecisionSnapshot
# ---------------------------------------------------------------------------

def _fetch_snapshots(db, since: datetime) -> List[Any]:
    """拉取 since 之后的已平仓 DecisionSnapshot（有 pnl 结果的）。"""
    try:
        from backend.database.models import DecisionSnapshot
    except Exception as err:  # noqa: BLE001
        logger.warning("[Dataset] DecisionSnapshot 导入失败: %s", err)
        return []
    try:
        rows = (
            db.query(DecisionSnapshot)
            .filter(DecisionSnapshot.timestamp >= since)
            .filter(DecisionSnapshot.pnl_pct.isnot(None))
            # [P0-4 消费端防御] SFT 监督数据只吃已执行决策：未执行快照的 pnl 回填
            # 来自模糊匹配，错配会把错误标签灌进训练集。
            .filter(DecisionSnapshot.executed == True)  # noqa: E712
            .order_by(DecisionSnapshot.timestamp.asc())
            .all()
        )
        return rows
    except Exception as err:  # noqa: BLE001
        logger.warning("[Dataset] 查询 DecisionSnapshot 失败: %s", err)
        return []


def _aggregate_window(snapshots: List[Any]) -> Optional[Dict[str, Any]]:
    """把一个窗口内的 snapshots 聚合成统计摘要。样本不足返回 None。"""
    if len(snapshots) < _MIN_TRADES_PER_WINDOW:
        return None

    pnls = [float(s.pnl_pct or 0) for s in snapshots]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    n = len(pnls)
    win_rate = len(wins) / n if n else 0
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0  # 取绝对值，便于比较
    net_pnl = sum(pnls)
    total_trades = n

    # regime 分布：取窗口内最常见的 regime_at_decision
    regimes = [s.regime_at_decision for s in snapshots if s.regime_at_decision]
    regime = max(set(regimes), key=regimes.count) if regimes else "unknown"

    # 手续费占比估算：DecisionSnapshot 无手续费字段，用 avg_loss 占比近似
    # （avg_loss 远大于 avg_win 时往往伴随高换手/手续费侵蚀）
    fee_proxy = 0.0
    if avg_win > 0 and avg_loss > 0 and avg_loss > avg_win:
        fee_proxy = (avg_loss - avg_win) / (avg_loss + avg_win) if (avg_loss + avg_win) > 0 else 0

    return {
        "regime": regime,
        "trades": total_trades,
        "win_rate": round(win_rate, 4),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "net_pnl": round(net_pnl, 4),
        "fee_gross_ratio": round(fee_proxy, 4),  # 近似，供 prompt 用
        "window_start": snapshots[0].timestamp,
        "window_end": snapshots[-1].timestamp,
    }


# ---------------------------------------------------------------------------
# 标签构造：根据窗口实际结果反推「应使用的参数」
# ---------------------------------------------------------------------------

def _clamp(val: float, key: str) -> float:
    lo, hi = _PARAM_BOUNDS[key]
    v = max(lo, min(hi, val))
    if key in ("scalp_min_confidence", "max_daily_trades"):
        v = float(int(round(v)))
    return v


def _label_params(stats: Dict[str, Any]) -> Tuple[Dict[str, float], str]:
    """根据窗口统计构造监督标签。

    策略（与 decision_feedback_service 的规则逻辑保持一致）：
      - net_pnl < 0 且 avg_loss > avg_win：提高盈亏比 + 收紧置信度（更保守）
      - fee_gross_ratio 高（手续费侵蚀）：收紧日交易上限
      - net_pnl > 0 且 胜率高：保持/适度放松（正向强化当前参数）
    返回 (建议参数 dict, reasoning 字符串)。
    """
    params = dict(_BASELINE_PARAMS)
    reasons: List[str] = []

    net = stats["net_pnl"]
    avg_win = stats["avg_win"]
    avg_loss = stats["avg_loss"]
    wr = stats["win_rate"]
    fee = stats["fee_gross_ratio"]

    if net < 0 and avg_loss > 0 and avg_loss > avg_win:
        # 亏损窗口 + 亏大于盈 → 收紧（学 decision_feedback 规则3）
        params["min_risk_reward"] = _clamp(2.0, "min_risk_reward")
        params["scalp_min_confidence"] = _clamp(72, "scalp_min_confidence")
        reasons.append(f"净亏({net:+.2f}%)且平均亏损({avg_loss:.2f})>平均盈利({avg_win:.2f})，提高盈亏比门槛")
    elif fee > 0.30:
        # 手续费侵蚀 → 降频（学 decision_feedback 规则2）
        params["max_daily_trades"] = _clamp(7, "max_daily_trades")
        params["min_risk_reward"] = _clamp(2.0, "min_risk_reward")
        reasons.append(f"手续费/亏损侵蚀指标偏高({fee:.2f})，收紧日交易上限并提高盈亏比")
    elif net > 0 and wr >= 0.5:
        # 盈利窗口 + 胜率合格 → 正向强化，适度放松（学 decision_feedback 规则4）
        params["min_risk_reward"] = _clamp(1.6, "min_risk_reward")
        params["scalp_min_confidence"] = _clamp(66, "scalp_min_confidence")
        reasons.append(f"净盈({net:+.2f}%)胜率{wr*100:.0f}%状态良好，适度放松以增加机会")
    else:
        # 中性：保持基准
        reasons.append("状态中性，维持基准参数")

    reasoning = "；".join(reasons)
    # 四舍五入显示
    params = {k: v for k, v in params.items()}
    return params, reasoning


def _label_v2(stats: Dict[str, Any], db=None) -> Tuple[Dict[str, float], str]:
    """预留：回测网格搜索算最优标签的升级接口。

    未来数据充足时，在此用 backtest_engine 对该窗口做参数网格搜索，
    选 Sharpe 最高的一组作为标签。当前未启用，返回 None 由调用方回退到 _label_params。
    """
    return {}, ""  # noqa: 未启用


# ---------------------------------------------------------------------------
# Prompt 构造（与 gate_optimizer_service 保持一致风格）
# ---------------------------------------------------------------------------

def _build_messages(stats: Dict[str, Any], target_params: Dict[str, float], reasoning: str) -> List[Dict[str, str]]:
    """构造一条 SFT 训练样本的 messages（system/user/assistant）。"""
    wr_str = f"{stats['win_rate'] * 100:.1f}%"

    user = (
        f"当前市场环境：{stats['regime']}\n"
        f"统计窗口（{_WINDOW_DAYS}天）：\n"
        f"- 交易笔数：{stats['trades']}\n"
        f"- 胜率：{wr_str}\n"
        f"- 平均盈利：{stats['avg_win']:.4f}\n"
        f"- 平均亏损：{stats['avg_loss']:.4f}\n"
        f"- 净盈亏：{stats['net_pnl']:+.4f}\n"
        f"- 手续费侵蚀指标：{stats['fee_gross_ratio']:.4f}\n\n"
        f"请输出建议的门控参数，严格 JSON 格式：\n"
        f'{{"min_risk_reward": <1.5-3.0>, '
        f'"scalp_min_confidence": <55-85整数>, '
        f'"max_daily_trades": <3-10整数>, '
        f'"confidence": <0-1>, '
        f'"reasoning": "<一句话理由>"}}'
    )

    assistant = json.dumps(
        {
            "min_risk_reward": target_params["min_risk_reward"],
            "scalp_min_confidence": target_params["scalp_min_confidence"],
            "max_daily_trades": target_params["max_daily_trades"],
            "confidence": 0.75,
            "reasoning": reasoning,
        },
        ensure_ascii=False,
    )

    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
        {"role": "assistant", "content": assistant},
    ]


# ---------------------------------------------------------------------------
# 主流程：滑窗生成数据集
# ---------------------------------------------------------------------------

def build_dataset(
    days: int = 90,
    out_path: str = "training/data/train.jsonl",
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """生成 SFT 训练数据集。

    Args:
        days: 回看总天数（从最近一笔交易往前）。
        out_path: 输出 JSONL 路径。
        dry_run: True 则只统计样本数和分布，不写文件。

    Returns:
        汇总（样本数、各 regime 分布、标签分布、输出路径）。
    """
    try:
        from backend.database.connection import AnalyticsSessionLocal
    except Exception as err:  # noqa: BLE001
        logger.error("[Dataset] 无法导入 AnalyticsSessionLocal: %s", err)
        return {"ok": False, "error": str(err)}

    db = AnalyticsSessionLocal()
    samples: List[Dict[str, Any]] = []
    regime_dist: Dict[str, int] = {}
    label_dist = {"tighten": 0, "relax": 0, "neutral": 0}

    try:
        # 1. 找数据时间范围
        from backend.database.models import DecisionSnapshot
        latest_row = (
            db.query(DecisionSnapshot)
            .filter(DecisionSnapshot.pnl_pct.isnot(None))
            .order_by(DecisionSnapshot.timestamp.desc())
            .first()
        )
        if not latest_row:
            logger.warning("[Dataset] 数据库无已平仓 DecisionSnapshot，无法生成数据集")
            return {"ok": False, "error": "no closed snapshots", "samples": 0}

        latest_ts = latest_row.timestamp
        since = latest_ts - timedelta(days=days)
        all_snaps = _fetch_snapshots(db, since)
        logger.info("[Dataset] %s~%s 共 %d 笔已平仓快照", since.date(), latest_ts.date(), len(all_snaps))

        if len(all_snaps) < _MIN_TRADES_PER_WINDOW:
            logger.warning("[Dataset] 总样本不足 %d，跳过", _MIN_TRADES_PER_WINDOW)
            return {"ok": False, "error": "insufficient total samples", "samples": 0}

        # 2. 滑窗聚合
        # [2026-07-11 修复] 原代码用 all_snaps[win_start_idx : win_start_idx+_WINDOW_DAYS]
        # 按"数组下标"切片——但 all_snaps 是逐笔交易快照（非逐日聚合），_WINDOW_DAYS=7
        # 在这里被当成"7笔"而不是"7天"，而 _aggregate_window 要求窗口内 ≥
        # _MIN_TRADES_PER_WINDOW(8) 笔才算有效——7<8 恒成立，导致每个窗口必被跳过，
        # 无论数据库里有多少笔历史交易，永远产出 0 条样本（本次实测：1442笔已平仓快照
        # 也被判定"insufficient"/"no valid windows"，dataset_builder 自上线以来
        # 实际从未真正产出过训练数据）。改为按真实时间窗口（_WINDOW_DAYS 天）过滤，
        # 语义与文档/变量名一致。
        win_start_ts = since
        while win_start_ts < latest_ts:
            win_end_ts = win_start_ts + timedelta(days=_WINDOW_DAYS)
            window = [s for s in all_snaps if win_start_ts <= s.timestamp < win_end_ts]
            win_start_ts += timedelta(days=_STEP_DAYS)

            stats = _aggregate_window(window)
            if stats is None:
                continue

            # 3. 构造标签
            target, reasoning = _label_params(stats)
            # 升级标签（若 _label_v2 启用）
            v2_target, v2_reason = _label_v2(stats, db)
            if v2_target:
                target, reasoning = v2_target, v2_reason

            # 4. 统计分布
            regime_dist[stats["regime"]] = regime_dist.get(stats["regime"], 0) + 1
            if stats["net_pnl"] < 0:
                label_dist["tighten"] += 1
            elif stats["net_pnl"] > 0 and stats["win_rate"] >= 0.5:
                label_dist["relax"] += 1
            else:
                label_dist["neutral"] += 1

            # 5. 构造样本
            messages = _build_messages(stats, target, reasoning)
            samples.append({"messages": messages, "_meta": {
                "regime": stats["regime"],
                "net_pnl": stats["net_pnl"],
                "window": f"{stats['window_start']}~{stats['window_end']}",
            }})

    finally:
        try:
            db.close()
        except Exception:
            pass

    if not samples:
        logger.warning("[Dataset] 滑窗后无有效样本（可能窗口样本普遍不足）")
        return {"ok": False, "error": "no valid windows", "samples": 0}

    # 6. 写文件（dry_run 跳过）
    written = 0
    if not dry_run:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            for s in samples:
                # 写标准 messages 格式（Unsloth/datasets 兼容），去掉 _meta
                f.write(json.dumps({"messages": s["messages"]}, ensure_ascii=False) + "\n")
                written += 1
        logger.info("[Dataset] 已写 %d 条到 %s", written, out_path)

    return {
        "ok": True,
        "samples": len(samples),
        "written": written,
        "out_path": out_path if not dry_run else None,
        "regime_distribution": regime_dist,
        "label_distribution": label_dist,
        "days_covered": days,
        "window_days": _WINDOW_DAYS,
        "step_days": _STEP_DAYS,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="生成本地 LLM 门控参数优化器的 SFT 训练数据集")
    p.add_argument("--days", type=int, default=90, help="回看天数（默认90）")
    p.add_argument("--out", default="training/data/train.jsonl", help="输出 JSONL 路径")
    p.add_argument("--dry-run", action="store_true", help="只统计不写文件")
    args = p.parse_args()

    print("=" * 60)
    print(f"生成训练数据集（days={args.days}, dry_run={args.dry_run}）")
    print("=" * 60)
    res = build_dataset(days=args.days, out_path=args.out, dry_run=args.dry_run)
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))

    # [2026-07-11 修复] Windows 默认控制台代码页为 GBK，print() 遇到 ✅/⚠️ 等
    # emoji 会抛 UnicodeEncodeError 导致进程非零退出（哪怕数据集其实已生成成功）。
    # 用纯文本前缀替代 emoji，跨平台安全。
    if res.get("ok"):
        print(f"\n[OK] 成功生成 {res['samples']} 条样本")
        if not args.dry_run:
            print(f"   输出：{res['out_path']}")
            print("   下一步：将此文件发送到 GPU 机的 training/data/ 目录进行微调")
        return 0
    else:
        print(f"\n[FAIL] 生成失败：{res.get('error', '未知')}")
        return 1


if __name__ == "__main__":
    sys.exit(_cli())
