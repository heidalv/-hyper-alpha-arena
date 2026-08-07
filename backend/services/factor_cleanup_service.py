"""FactorCleanupService — 因子批量清洗管线（2026-07-21 专项5）

背景：
    factor_engine/factors/ 目录有 1144 个因子文件。
    报告指出 945 个有权重因子中 75.7% 权重=1.0（等权 fallback），
    IC<0 的因子 455 个（比正贡献的 446 还多）。
    现有 factor_ic_evaluator 只做降权，不淘汰。

本服务：
    1. run_batch_ic_cleanup() — 对全部注册因子跑多币种批量 IC 评估
    2. 自动淘汰负贡献因子 / 标记噪音因子 / 保留 top-N 高 IC 因子
    3. 通过 CustomFactorStore 持久化状态，FactorEvaluationPipeline 读取状态过滤

调用方式：
    from backend.services.factor_cleanup_service import run_batch_ic_cleanup
    report = run_batch_ic_cleanup(db, lookback_days=30)
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 配置常量 ──
_CLEANUP_REPORT_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data", "factor_cleanup_report.json",
)
_LAST_RUN_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data", "factor_cleanup_last_run.json",
)
_CLEANUP_INTERVAL_SEC = 7 * 24 * 3600  # 7天一次

# 淘汰阈值
IC_REJECT_THRESHOLD = -0.05   # IC <= -5% 且样本足够 → rejected
IC_NOISE_THRESHOLD = 0.02      # |IC| < 2% 且样本充足 → low_signal
MIN_SAMPLES_REJECT = 20        # rejected 判定最少样本
MIN_SAMPLES_NOISE = 30         # low_signal 判定最少样本
MAX_ACTIVE_FACTORS = 50        # 保留 top-N 高 IC 因子为 active

# 评估用币种（覆盖大中小盘）
_DEFAULT_SYMBOLS = ["BTC", "ETH", "SOL", "DOGE", "LINK"]
_DEFAULT_TIMEFRAME = "1h"
_FORWARD_PERIOD = 5


def _load_klines_df(symbol: str, timeframe: str, count: int = 200):
    """加载 K 线为 DataFrame（复用 FactorService 的逻辑但独立实现以避免循环依赖）。"""
    import pandas as pd
    try:
        from backend.services.kline_data_service import kline_service
        raw = kline_service.get_klines_from_db(symbol, timeframe, count=count)
        if raw and len(raw) >= 20:
            return pd.DataFrame(raw)
    except Exception as exc:
        logger.debug("[FactorCleanup] %s %s K线加载失败: %s", symbol, timeframe, exc)
    return None


def _get_all_factor_ids() -> List[str]:
    """获取全部已注册因子 ID。"""
    try:
        from backend.services.factor_engine.factor_registry import registry
        from backend.services.factor_engine.factor_service import FactorService
        svc = FactorService()
        svc._ensure_registry_loaded()
        return list(registry._factors.keys())
    except Exception as exc:
        logger.warning("[FactorCleanup] 获取因子注册表失败: %s", exc)
        return []


def run_batch_ic_cleanup(
    db=None,
    *,
    lookback_days: int = 30,
    symbols: Optional[List[str]] = None,
    timeframe: str = _DEFAULT_TIMEFRAME,
    forward_period: int = _FORWARD_PERIOD,
    force: bool = False,
) -> Dict[str, Any]:
    """批量 IC 评估 + 自动淘汰管线。

    Args:
        db: 数据库 session（可选，当前不需要直接查询 DB）
        lookback_days: 回溯天数（用于报告记录）
        symbols: 评估用币种列表
        timeframe: K 线周期
        forward_period: IC 前瞻期
        force: 强制运行（忽略节流）

    Returns:
        清洗报告 dict
    """
    # ── 节流：7天一次 ──
    if not force and _is_throttled():
        logger.info("[FactorCleanup] 距上次运行不足7天，跳过")
        return {"skipped": True, "reason": "throttled"}

    syms = symbols or _DEFAULT_SYMBOLS
    factor_ids = _get_all_factor_ids()
    if not factor_ids:
        logger.warning("[FactorCleanup] 无已注册因子，跳过")
        return {"skipped": True, "reason": "no_factors", "total_factors": 0}

    logger.info(
        "[FactorCleanup] 开始批量IC评估: %d 个因子 × %d 币种 (%s)",
        len(factor_ids), len(syms), ", ".join(syms),
    )

    # ── 多币种批量计算因子 IC ──
    from backend.services.factor_engine.factor_calculator import FactorCalculator
    from backend.services.factor_engine.factor_evaluator import FactorEvaluator

    calc = FactorCalculator()
    evaluator = FactorEvaluator(forward_period=forward_period)

    # factor_id → [ic across symbols]
    factor_ic_map: Dict[str, List[Tuple[float, int]]] = defaultdict(list)

    for sym in syms:
        df = _load_klines_df(sym, timeframe)
        if df is None or df.empty or "close" not in df.columns:
            logger.debug("[FactorCleanup] %s K线不足，跳过", sym)
            continue

        try:
            series_map = calc.calculate(factor_ids, df, symbol=sym, timeframe=timeframe)
        except Exception as exc:
            logger.warning("[FactorCleanup] %s 因子计算失败: %s", sym, exc)
            continue

        close = df["close"]
        for fid, factor_values in series_map.items():
            if factor_values is None or len(factor_values) < 30:
                continue
            try:
                report = evaluator.evaluate_factor(
                    fid, factor_values, close, forward_period=forward_period,
                )
                if report.data_points >= MIN_SAMPLES_REJECT:
                    factor_ic_map[fid].append((report.ic_mean, report.data_points))
            except Exception:
                pass  # 单因子评估失败不影响其他

    if not factor_ic_map:
        logger.warning("[FactorCleanup] 所有币种因子评估均无有效结果")
        return {"skipped": True, "reason": "no_valid_results", "total_factors": len(factor_ids)}

    # ── 聚合：多币种平均 IC ──
    factor_stats: Dict[str, Dict[str, Any]] = {}
    for fid, ic_list in factor_ic_map.items():
        avg_ic = sum(ic for ic, _ in ic_list) / len(ic_list)
        total_samples = sum(n for _, n in ic_list)
        factor_stats[fid] = {
            "avg_ic": round(avg_ic, 5),
            "total_samples": total_samples,
            "symbols_evaluated": len(ic_list),
            "abs_ic": round(abs(avg_ic), 5),
        }

    # ── 分类：rejected / low_signal / active ──
    rejected: List[str] = []
    low_signal: List[str] = []
    active_candidates: List[Tuple[str, float]] = []

    for fid, stats in factor_stats.items():
        avg_ic = stats["avg_ic"]
        samples = stats["total_samples"]

        if avg_ic <= IC_REJECT_THRESHOLD and samples >= MIN_SAMPLES_REJECT:
            rejected.append(fid)
        elif abs(avg_ic) < IC_NOISE_THRESHOLD and samples >= MIN_SAMPLES_NOISE:
            low_signal.append(fid)
        else:
            active_candidates.append((fid, avg_ic))

    # active: 按 IC 绝对值排序取 top-N
    active_candidates.sort(key=lambda x: abs(x[1]), reverse=True)
    active_top = [fid for fid, _ in active_candidates[:MAX_ACTIVE_FACTORS]]
    # 超出 top-N 的降级为 low_signal
    overflow = [fid for fid, _ in active_candidates[MAX_ACTIVE_FACTORS:]]
    low_signal.extend(overflow)

    # ── 持久化到 CustomFactorStore ──
    try:
        from backend.services.factor_engine.custom_factor_store import custom_factor_store
        for fid in rejected:
            custom_factor_store.set_status(fid, "rejected")
        for fid in low_signal:
            custom_factor_store.set_status(fid, "low_signal")
        for fid in active_top:
            custom_factor_store.set_status(fid, "active")
        logger.info(
            "[FactorCleanup] CustomFactorStore 更新完成: "
            "rejected=%d, low_signal=%d, active=%d",
            len(rejected), len(low_signal), len(active_top),
        )
    except Exception as exc:
        logger.warning("[FactorCleanup] CustomFactorStore 更新失败(降级为只写报告): %s", exc)

    # ── 产出报告 ──
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lookback_days": lookback_days,
        "symbols": syms,
        "timeframe": timeframe,
        "forward_period": forward_period,
        "total_factors_registered": len(factor_ids),
        "factors_evaluated": len(factor_stats),
        "summary": {
            "rejected": len(rejected),
            "low_signal": len(low_signal),
            "active": len(active_top),
        },
        "rejected_factors": sorted(
            [{"factor_id": f, **factor_stats[f]} for f in rejected],
            key=lambda x: x["avg_ic"],
        ),
        "active_top": sorted(
            [{"factor_id": f, **factor_stats[f]} for f in active_top],
            key=lambda x: abs(x["avg_ic"]),
            reverse=True,
        ),
        "low_signal_count": len(low_signal),
    }

    _write_report(report)
    _mark_run_time()

    logger.info(
        "[FactorCleanup] 完成: 评估=%d, rejected=%d, low_signal=%d, active=%d",
        len(factor_stats), len(rejected), len(low_signal), len(active_top),
    )
    return report


def get_rejected_factor_ids() -> set:
    """获取被淘汰的因子 ID 集合（供 FactorEvaluationPipeline 调用过滤）。"""
    try:
        from backend.services.factor_engine.custom_factor_store import custom_factor_store
        return {r["factor_id"] for r in custom_factor_store.list(status="rejected")}
    except Exception:
        return set()


def get_low_signal_factor_ids() -> set:
    """获取低信号因子 ID 集合。"""
    try:
        from backend.services.factor_engine.custom_factor_store import custom_factor_store
        return {r["factor_id"] for r in custom_factor_store.list(status="low_signal")}
    except Exception:
        return set()


def _is_throttled() -> bool:
    """检查是否在节流期内。"""
    try:
        if not os.path.exists(_LAST_RUN_FILE):
            return False
        with open(_LAST_RUN_FILE, "r") as f:
            data = json.load(f)
        last_ts = float(data.get("ts", 0))
        return (time.time() - last_ts) < _CLEANUP_INTERVAL_SEC
    except Exception:
        return False


def _mark_run_time() -> None:
    """记录本次运行时间。"""
    try:
        os.makedirs(os.path.dirname(_LAST_RUN_FILE), exist_ok=True)
        with open(_LAST_RUN_FILE, "w") as f:
            json.dump({"ts": time.time(), "at": datetime.now(timezone.utc).isoformat()}, f)
    except Exception as exc:
        logger.debug("[FactorCleanup] 记录运行时间失败: %s", exc)


def _write_report(report: dict) -> None:
    """写入清洗报告 JSON。"""
    try:
        os.makedirs(os.path.dirname(_CLEANUP_REPORT_FILE), exist_ok=True)
        with open(_CLEANUP_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.warning("[FactorCleanup] 报告写入失败: %s", exc)
