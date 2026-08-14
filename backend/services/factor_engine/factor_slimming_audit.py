"""
因子瘦身审计 — factor_slimming_audit.py（P0，规划文档 §3.2）。

背景：Registry 里 1144 个因子中 AI 生成因子占 1019 个，存在大量近重名冗余
（如 ai_gen_fake_break / ai_gen_fake_breakout / ai_gen_fakbrk 几乎同名），且没有
任何自动化机制淘汰"从未有效"或"与已保留因子高度冗余"的因子——1144 个因子里
纯运气也能在 p=0.05 水平下产出 50+ 个看似"IC显著"的因子，这正是需要 DSR/PBO
硬门槛 + 因子瘦身两条腿一起走的原因（本文件负责瘦身，硬门槛见 factor_evolution_loop.py）。

三分类标准：
    保留(active):     |IC|>=0.02 且 ICIR>=0.3 且 与已保留因子池最大相关<0.5
    隔离(quarantine):  IC/ICIR 达标但与池相关>=0.5（冗余）；或 ICIR 在 [0.15, 0.3) 之间（转弱但未彻底失效）
    淘汰(archive):     |IC|<0.02，或 ICIR<0.15（近乎无预测力），或 零方差/常数因子

处理动作（避免误杀）：
    - 淘汰候选：不立即删除代码。先在 factor_runtime_weights.json 中降权 50%，
      记录 pending_archive_since 时间戳。下次审计（跨越 ARCHIVE_OBSERVE_DAYS 天）
      若依旧判定淘汰，才把源码文件物理移入 factors/_ai_gen_archive/（FactorLoader
      按约定跳过下划线开头目录，故移入即等于从 Registry 移除，但源码可回滚）。
    - 隔离候选：直接移入 factors/_ai_gen_quarantine/（60 天后的下次审计里，脚本会
      重新导入该文件独立测试，判断是否满足"恢复"条件；满足则移回原分类目录）。

用法：
    python -m backend.services.factor_engine.factor_slimming_audit                 # 只出报告，不改任何文件
    python -m backend.services.factor_engine.factor_slimming_audit --apply         # 按上面规则真正执行降权/物理隔离
    python -m backend.services.factor_engine.factor_slimming_audit --symbols BTC,ETH,SOL --days 90

输出：
    data/factor_slimming_report_{date}.json  — 完整审计报告（每因子分类+指标+理由）
    data/factor_slimming_state.json          — 跨轮次状态（pending_archive_since 等），供下次审计判断是否到期物理归档
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# 1019个AI生成因子里有相当一部分是常数/近常数（本身就是本脚本要淘汰的对象），
# scipy.stats.pearsonr 对每个常数窗口都会抛 ConstantInputWarning，在
# time_series_ic 的滚动窗口场景下单个因子就能刷出几千行——不是错误，只是
#"这个因子该被判定为archive"的噪音副产物，抑制掉避免日志膨胀拖慢真正的审计进度。
warnings.filterwarnings("ignore", message=".*constant.*", category=RuntimeWarning)
try:
    from scipy.stats import ConstantInputWarning as _ScipyConstantInputWarning
    warnings.filterwarnings("ignore", category=_ScipyConstantInputWarning)
except Exception:
    pass

logger = logging.getLogger(__name__)

DEFAULT_SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP"]
DEFAULT_PERIOD = "1h"
ARCHIVE_OBSERVE_DAYS = 14   # 淘汰候选先降权观察期
# [2026-08-14 P1-D3] 隔离候选观察期：首次判定 quarantine 时先降权观察，
# 观察期满仍判隔离才物理移文件（此前首次命中即物理移动，无观察期、无恢复）。
QUARANTINE_OBSERVE_DAYS = 14
QUARANTINE_RECHECK_DAYS = 60

IC_MIN_ABS = 0.02
ICIR_ACTIVE_MIN = 0.30
ICIR_WEAK_MIN = 0.15       # < 此值直接淘汰候选；[0.15, 0.30) 之间为隔离候选
POOL_MAX_CORR = 0.50

STATE_PATH = os.path.join("data", "factor_slimming_state.json")
WEIGHTS_PATH = os.path.join("data", "factor_runtime_weights.json")
FACTORS_DIR = Path(__file__).parent / "factors"
QUARANTINE_DIR = FACTORS_DIR / "_ai_gen_quarantine"
ARCHIVE_DIR = FACTORS_DIR / "_ai_gen_archive"


def _load_klines(symbols: list[str], period: str, days: int) -> dict[str, pd.DataFrame]:
    from backend.services.data_center import data_center

    bars_per_day = {"1h": 24, "4h": 6, "15m": 96, "5m": 288}.get(period, 24)
    count = int(days * bars_per_day) + 50
    dfs: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            result = data_center.get_klines(sym, period, count=count)
            df = result.to_dataframe()
            if len(df) >= 100:
                dfs[sym] = df
        except Exception as e:
            logger.warning(f"[Slimming] 取数失败 {sym}: {e}")
    return dfs


def _forward_returns(df: pd.DataFrame, horizon: int = 5) -> pd.Series:
    close = df["close"].astype(float)
    fwd = close.shift(-horizon) / close - 1.0
    return fwd


def _audit_one_factor(factor_id: str, factor_cls, dfs: dict[str, pd.DataFrame]) -> dict[str, Any] | None:
    """对单个因子在所有品种上算 IC 序列/ICIR，返回聚合指标（跨品种取均值）。"""
    from backend.services.factor_engine.evaluation import (
        information_coefficient, time_series_ic, compute_icir,
    )

    all_ics: list[float] = []
    per_symbol_ic: dict[str, float] = {}
    value_series_for_corr: pd.Series | None = None
    is_constant = True

    for sym, df in dfs.items():
        try:
            inst = factor_cls({})
            series = inst.calculate(df)
            if series is None:
                continue
            series = pd.Series(np.asarray(series, dtype=float), index=df.index[-len(series):]) \
                if not isinstance(series, pd.Series) else series
            fwd = _forward_returns(df, horizon=5)
            aligned = pd.DataFrame({"f": series, "r": fwd}).dropna()
            if len(aligned) < 30:
                continue
            if np.std(aligned["f"].values) > 1e-9:
                is_constant = False
            ic_series = time_series_ic(aligned["f"], aligned["r"])
            if len(ic_series) > 0:
                all_ics.extend([float(x) for x in ic_series if np.isfinite(x)])
            ic_full = information_coefficient(aligned["f"].values, aligned["r"].values)
            per_symbol_ic[sym] = round(ic_full, 4)
            if value_series_for_corr is None and len(aligned) > 60:
                # 取样本最长的一条品种序列供 pool 相关性比较（跨因子对齐用 z-score 后按位比较）
                z = (aligned["f"] - aligned["f"].mean()) / (aligned["f"].std() + 1e-9)
                value_series_for_corr = z.reset_index(drop=True)
        except Exception as e:
            logger.debug(f"[Slimming] {factor_id}@{sym} 计算失败: {e}")
            continue

    if not per_symbol_ic:
        return None

    ic_mean = float(np.mean(list(per_symbol_ic.values())))
    icir = compute_icir(np.asarray(all_ics)) if all_ics else 0.0

    return {
        "factor_id": factor_id,
        "ic_mean": round(ic_mean, 4),
        "icir": round(icir, 4),
        "per_symbol_ic": per_symbol_ic,
        "is_constant": is_constant,
        "corr_probe_series": value_series_for_corr,
    }


def _greedy_pool_corr(candidates: list[dict], max_corr: float = POOL_MAX_CORR) -> dict[str, float]:
    """按 ICIR 降序贪心建池：能进池则记 corr=0（视为不冗余）；否则记与池内已有因子最大相关。

    退化保护：没有 corr_probe_series（数据不足）的因子直接给 corr=0（不因数据问题误判冗余）。
    """
    ranked = sorted(
        [c for c in candidates if c.get("corr_probe_series") is not None],
        key=lambda c: c["icir"], reverse=True,
    )
    pool: list[pd.Series] = []
    max_corr_map: dict[str, float] = {}
    for c in ranked:
        s = c["corr_probe_series"]
        if not pool:
            pool.append(s)
            max_corr_map[c["factor_id"]] = 0.0
            continue
        best = 0.0
        n = min(len(s), *(len(p) for p in pool))
        if n < 30:
            max_corr_map[c["factor_id"]] = 0.0
            pool.append(s)
            continue
        for p in pool:
            m = min(len(s), len(p))
            try:
                corr = float(np.corrcoef(s.values[:m], p.values[:m])[0, 1])
            except Exception:
                corr = 0.0
            if np.isfinite(corr):
                best = max(best, abs(corr))
        max_corr_map[c["factor_id"]] = round(best, 4)
        if best < max_corr:
            pool.append(s)
    return max_corr_map


def _classify(ic_mean: float, icir: float, is_constant: bool, max_corr: float) -> tuple[str, str]:
    if is_constant:
        return "archive", "零方差/常数因子，无区分力"
    if abs(ic_mean) < IC_MIN_ABS:
        return "archive", f"|IC|={abs(ic_mean):.4f} < {IC_MIN_ABS} 门槛"
    if icir < ICIR_WEAK_MIN:
        return "archive", f"ICIR={icir:.4f} < {ICIR_WEAK_MIN}，近乎无预测力"
    if icir < ICIR_ACTIVE_MIN:
        return "quarantine", f"ICIR={icir:.4f} 在 [{ICIR_WEAK_MIN},{ICIR_ACTIVE_MIN}) 转弱区间"
    if max_corr >= POOL_MAX_CORR:
        return "quarantine", f"与已保留因子池最大相关={max_corr:.3f} >= {POOL_MAX_CORR}，冗余"
    return "active", f"IC={ic_mean:.4f} ICIR={icir:.4f} max_corr={max_corr:.3f} 全部达标"


def _load_json(path: str) -> dict:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_tradable_factor_ids() -> set:
    """[2026-08-14 P1-D3] factor_active_set 中可交易行（ACTIVE/SMALL_LIVE/PAPER）
    的因子 id —— 受保护集：审计只报告不移动，防止正在实盘使用的因子被物理删源。"""
    try:
        from backend.database.connection import AnalyticsSessionLocal
        from backend.database.models import FactorActiveSet
        db = AnalyticsSessionLocal()
        try:
            rows = (
                db.query(FactorActiveSet.factor_id)
                .filter(FactorActiveSet.state.in_(["ACTIVE", "SMALL_LIVE", "PAPER"]))
                .all()
            )
            return {str(fid) for (fid,) in rows if fid}
        finally:
            db.close()
    except Exception as e:
        logger.warning(
            "[Slimming] factor_active_set 保护集读取失败（保护集为空，物理移动风险上升）: %s", e
        )
        return set()


def restore_quarantined_factors() -> dict:
    """[2026-08-14 P1-D3] 恢复逻辑：把隔离区文件移回原分类目录（按 state 记录的 src）。

    人工/运维触发（因子隔离 60 天后需要复核时，或误隔离时）：
        from backend.services.factor_engine.factor_slimming_audit import restore_quarantined_factors
        restore_quarantined_factors()
    """
    state = _load_json(STATE_PATH)
    restored = []
    for fid, st in list((state or {}).items()):
        if not isinstance(st, dict) or st.get("status") != "quarantined":
            continue
        src_str = st.get("src")
        if not src_str:
            continue
        src = Path(src_str)
        dest = Path(QUARANTINE_DIR) / src.name
        try:
            if not src.exists() and dest.exists():
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dest), str(src))
                st["status"] = "restored"
                st["restored_at"] = datetime.now(timezone.utc).isoformat()
                restored.append(fid)
                logger.info("[Slimming] 已恢复隔离因子 %s → %s", fid, src)
        except Exception as e:
            logger.warning("[Slimming] 恢复 %s 失败: %s", fid, e)
    if restored:
        _save_json(STATE_PATH, state)
    return {"restored": restored}


def _move_factor_file(factor_cls, dest_dir: Path, dry_run: bool) -> str | None:
    import inspect
    try:
        src = Path(inspect.getfile(factor_cls))
    except Exception:
        return None
    if not src.exists() or src.parent == dest_dir:
        return None
    if dry_run:
        return f"[dry-run] 将移动 {src} -> {dest_dir}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists():
        return f"跳过（目标已存在）: {dest}"
    shutil.move(str(src), str(dest))
    return f"已移动 {src.name} -> {dest_dir.name}/"


def run_audit(symbols: list[str] | None = None, period: str = DEFAULT_PERIOD,
              days: int = 90, apply_changes: bool = False) -> dict[str, Any]:
    from backend.services.factor_engine.factor_loader import FactorLoader
    from backend.services.factor_engine.factor_registry import registry

    syms = symbols or DEFAULT_SYMBOLS
    logger.info(f"[Slimming] 审计启动: symbols={syms} period={period} days={days} apply={apply_changes}")

    FactorLoader().discover_and_load_all()
    dfs = _load_klines(syms, period, days)
    if not dfs:
        return {"error": "取数失败，无可用K线数据"}

    factor_ids = registry.list_factors()
    logger.info(f"[Slimming] Registry 共 {len(factor_ids)} 个因子，开始逐个审计...")

    results = []
    n_done = 0
    for fid in factor_ids:
        try:
            factor_cls = registry._factors.get(fid)
            if factor_cls is None:
                continue
            r = _audit_one_factor(fid, factor_cls, dfs)
            if r is not None:
                r["factor_cls"] = factor_cls
                results.append(r)
        except Exception as e:
            logger.debug(f"[Slimming] {fid} 审计异常: {e}")
        n_done += 1
        if n_done % 200 == 0:
            logger.info(f"[Slimming] 进度 {n_done}/{len(factor_ids)}")

    logger.info(f"[Slimming] 成功获取指标 {len(results)}/{len(factor_ids)} 个因子，开始 pool 相关性建池...")
    corr_map = _greedy_pool_corr(results)

    runtime_weights = _load_json(WEIGHTS_PATH)
    weights = runtime_weights.get("weights", {})
    state = _load_json(STATE_PATH)
    now = datetime.now(timezone.utc)

    # [2026-08-14 P1-D3] 受保护因子：factor_active_set 可交易行（ACTIVE/SMALL_LIVE/
    # PAPER）只报告不移动，防止每周二 3:30 的自动审计物理移除实盘因子。
    protected = _load_tradable_factor_ids()
    if protected:
        logger.info("[Slimming] 受保护因子（factor_active_set 可交易）%d 个：只报告不移动", len(protected))

    # [2026-08-14 P1-D3] 恢复检查：曾隔离因子若源文件已回来（人工/脚本恢复）→ 清状态
    for _fid in list(state.keys()):
        _st = state.get(_fid) or {}
        if isinstance(_st, dict) and _st.get("status") == "quarantined" and _st.get("src"):
            if os.path.exists(str(_st["src"])):
                del state[_fid]
                logger.info("[Slimming] 检测到隔离因子 %s 已恢复，清除状态", _fid)

    report_items = []
    counts = {"active": 0, "quarantine": 0, "archive": 0}
    actions = []

    for r in results:
        fid = r["factor_id"]
        max_corr = corr_map.get(fid, 0.0)
        cls, reason = _classify(r["ic_mean"], r["icir"], r["is_constant"], max_corr)
        counts[cls] += 1

        item = {
            "factor_id": fid,
            "classification": cls,
            "reason": reason,
            "ic_mean": r["ic_mean"],
            "icir": r["icir"],
            "max_corr_with_pool": max_corr,
            "runtime_weight": weights.get(fid),
        }

        prev_state = state.get(fid, {})
        if fid in protected and cls in ("archive", "quarantine"):
            # 受保护：只报告，不降权、不移动
            item["action"] = "protected_report_only"
            item["protected"] = True
            if fid in state:
                del state[fid]
        elif cls == "archive":
            if prev_state.get("status") == "pending_archive":
                since = prev_state.get("since")
                try:
                    since_dt = datetime.fromisoformat(since) if since else now
                except Exception:
                    since_dt = now
                if (now - since_dt) >= timedelta(days=ARCHIVE_OBSERVE_DAYS):
                    # 观察期已满仍判淘汰 → 物理归档
                    msg = _move_factor_file(r["factor_cls"], ARCHIVE_DIR, dry_run=not apply_changes)
                    item["action"] = "physically_archived" if apply_changes else "would_archive"
                    if msg:
                        actions.append(f"{fid}: {msg}")
                    state[fid] = {"status": "archived", "since": now.isoformat()}
                else:
                    item["action"] = "observing"
                    item["pending_since"] = prev_state.get("since")
                    state[fid] = prev_state
            else:
                # 首次判定淘汰：降权50%观察，不立即物理移除
                if apply_changes:
                    weights[fid] = round(float(weights.get(fid, 1.0) or 1.0) * 0.5, 4)
                item["action"] = "downweighted_50pct_observing" if apply_changes else "would_downweight"
                state[fid] = {"status": "pending_archive", "since": now.isoformat()}
        elif cls == "quarantine":
            # [2026-08-14 P1-D3 修复] 观察期状态机：首次判定 → 降权观察
            # （pending_quarantine）；观察期满仍判隔离 → 才物理移动并记录 src 供恢复。
            if prev_state.get("status") == "pending_quarantine":
                since = prev_state.get("since")
                try:
                    since_dt = datetime.fromisoformat(since) if since else now
                except Exception:
                    since_dt = now
                if (now - since_dt) >= timedelta(days=QUARANTINE_OBSERVE_DAYS):
                    import inspect as _inspect
                    try:
                        _src = str(Path(_inspect.getfile(r["factor_cls"])))
                    except Exception:
                        _src = None
                    msg = _move_factor_file(r["factor_cls"], QUARANTINE_DIR, dry_run=not apply_changes)
                    item["action"] = "quarantined" if apply_changes else "would_quarantine"
                    if msg:
                        actions.append(f"{fid}: {msg}")
                    state[fid] = {
                        "status": "quarantined",
                        "since": now.isoformat(),
                        "src": _src,   # 供 restore_quarantined_factors 恢复
                    }
                else:
                    item["action"] = "observing_quarantine"
                    item["pending_since"] = prev_state.get("since")
                    state[fid] = prev_state
            else:
                # 首次判定隔离：降权50%观察（与 archive 一致），不立即物理移除
                if apply_changes:
                    weights[fid] = round(float(weights.get(fid, 1.0) or 1.0) * 0.5, 4)
                item["action"] = "downweighted_50pct_observing_quarantine" if apply_changes else "would_downweight"
                state[fid] = {"status": "pending_quarantine", "since": now.isoformat()}
        else:
            # 恢复为 active：清掉旧的 pending/quarantine 状态标记
            if fid in state:
                del state[fid]

        report_items.append(item)

    if apply_changes:
        runtime_weights["weights"] = weights
        runtime_weights["updated_at"] = now.isoformat()
        _save_json(WEIGHTS_PATH, runtime_weights)
        _save_json(STATE_PATH, state)

    report = {
        "audit_at": now.isoformat(),
        "symbols": syms,
        "period": period,
        "days": days,
        "applied": apply_changes,
        "registry_total": len(factor_ids),
        "audited_total": len(results),
        "counts": counts,
        "target_range": "200-300 (§3.2)",
        "actions_taken": actions,
        "items": sorted(report_items, key=lambda x: x["icir"], reverse=True),
    }

    date_str = now.strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join("data", f"factor_slimming_report_{date_str}.json")
    _save_json(report_path, report)
    logger.info(
        f"[Slimming] 审计完成: registry={len(factor_ids)} 审计成功={len(results)} "
        f"active={counts['active']} quarantine={counts['quarantine']} archive={counts['archive']} "
        f"报告→{report_path}"
    )
    report["report_path"] = report_path
    return report


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="因子瘦身审计")
    parser.add_argument("--symbols", type=str, default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--period", type=str, default=DEFAULT_PERIOD)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--apply", action="store_true", help="真正执行降权/物理隔离，不传则只出报告")
    args = parser.parse_args()

    syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
    report = run_audit(syms, args.period, args.days, apply_changes=args.apply)
    print(json.dumps({k: v for k, v in report.items() if k != "items"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
