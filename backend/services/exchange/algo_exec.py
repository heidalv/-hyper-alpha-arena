"""
OrderAlgo 执行驱动器（阶段 3.2）。

把 algo.py 的纯函数切片（twap / pov / funding_is / sor_route）应用到真实下单回调。

职责:
- 解析 OrderAlgo + algo_config → 子单序列（含数据不可得时的降级规则）
- 按序执行: place_fn(child_qty, is_last) -> result（片间 sleep 间隔）
- 返回审计元数据（algo / slices / fallback），供 OrderResult.raw / 日志留痕

降级规则（数据不可得时保持可用，不阻断主链，日志告警）:
- POV: 无实时成交量 forecast → 降级 TWAP
- SOR: 无多 venue 报价 → 单笔 MARKET
- FUNDING_IS: 无 funding rate → 按 0 处理（等价 TWAP 均分）

注意: 子单的 TP/SL 由调用方自行决定（实盘建议仅最后一片携带，避免重复触发单）。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from backend.services.execution.algo import (
    AlgoConfig,
    ChildOrder,
    funding_is,
    pov,
    sor_route,
    twap,
)

logger = logging.getLogger(__name__)

# 切片上限护栏：防 AI/配置异常导致海量子单
MAX_SLICES = 20
MIN_SLICE_QTY = 1e-9


@dataclass(frozen=True)
class AlgoSlice:
    """执行层子单（algo.py ChildOrder 的增强：带 is_last 标记）。"""
    qty: float
    delay_ms: float
    is_last: bool = False
    algo_hint: str = "MARKET"
    limit_price: Optional[float] = None


def _coerce_config(algo_config: Optional[Dict[str, Any]]) -> AlgoConfig:
    """把 dict 配置转为 AlgoConfig（缺省用默认值）。"""
    cfg = AlgoConfig()
    if not algo_config or not isinstance(algo_config, dict):
        return cfg
    try:
        if "twap_slices" in algo_config:
            cfg.twap_slices = max(1, int(algo_config["twap_slices"]))
        if "twap_interval_ms" in algo_config:
            cfg.twap_interval_ms = max(0.0, float(algo_config["twap_interval_ms"]))
        if "pov_participation" in algo_config:
            cfg.pov_participation = max(0.0, min(1.0, float(algo_config["pov_participation"])))
        if "pov_max_duration_ms" in algo_config:
            cfg.pov_max_duration_ms = max(0.0, float(algo_config["pov_max_duration_ms"]))
        if "funding_threshold_bps" in algo_config:
            cfg.funding_threshold_bps = max(0.0, float(algo_config["funding_threshold_bps"]))
        if "expected_hold_ms" in algo_config:
            cfg.expected_hold_ms = max(0.0, float(algo_config["expected_hold_ms"]))
    except Exception as e:
        logger.warning(f"[AlgoExec] algo_config 解析异常（用默认值）: {e}")
    return cfg


def _to_slices(children: List[ChildOrder]) -> List[AlgoSlice]:
    """ChildOrder → AlgoSlice（补 is_last + 上限护栏）。"""
    children = list(children)[:MAX_SLICES]
    out: List[AlgoSlice] = []
    n = len(children)
    for i, c in enumerate(children):
        if c.qty < MIN_SLICE_QTY:
            continue
        out.append(AlgoSlice(
            qty=c.qty,
            delay_ms=max(0.0, float(c.delay_ms)),
            is_last=(i == n - 1),
            algo_hint=c.algo_hint,
            limit_price=c.limit_price,
        ))
    return out


def build_algo_slices(
    parent_qty: float,
    algo: Optional[str],
    algo_config: Optional[Dict[str, Any]] = None,
    *,
    funding_rate_8h: Optional[float] = None,
    volume_forecast_fn: Optional[Callable[[float], float]] = None,
    venue_quotes: Optional[Dict[str, tuple[float, float]]] = None,
) -> tuple[List[AlgoSlice], Dict[str, Any]]:
    """OrderAlgo + 父单数量 → 子单序列 + 审计元数据。

    Args:
        parent_qty: 父单数量（>0）
        algo: OrderAlgo 值（MARKET/TWAP/POV/FUNDING_IS/SOR，None/未知 → MARKET）
        algo_config: 算法配置 dict
        funding_rate_8h: FUNDING_IS 需要的 8h funding rate（None → 按 0，等价 TWAP）
        volume_forecast_fn: POV 需要的成交量累计预测函数（None → 降级 TWAP）
        venue_quotes: SOR 需要的 {venue: (price, size)}（None/单 venue → 单笔 MARKET）

    Returns:
        (children, meta)；meta = {"algo", "slices", "fallback"}（fallback 为 None 表示无降级）
    """
    algo = (algo or "MARKET").upper()
    meta: Dict[str, Any] = {"algo": algo, "slices": 0, "fallback": None}

    if parent_qty <= MIN_SLICE_QTY:
        return [], meta

    cfg = _coerce_config(algo_config)

    if algo == "TWAP":
        children = twap(parent_qty, cfg)
    elif algo == "FUNDING_IS":
        fr = funding_rate_8h if funding_rate_8h is not None else 0.0
        if funding_rate_8h is None:
            meta["fallback"] = "funding_rate_unavailable→use_0(≈TWAP)"
        children, _cost = funding_is(parent_qty, fr, cfg)
    elif algo == "POV":
        if volume_forecast_fn is None:
            meta["fallback"] = "pov_no_volume_forecast→twap"
            children = twap(parent_qty, cfg)
        else:
            children = pov(parent_qty, volume_forecast_fn, cfg)
    elif algo == "SOR":
        if not venue_quotes or len(venue_quotes) < 2:
            meta["fallback"] = "sor_single_venue→market"
            children = [ChildOrder(qty=parent_qty, delay_ms=0.0)]
        else:
            routing = sor_route(parent_qty, venue_quotes, cfg)
            children = [
                ChildOrder(qty=q, delay_ms=0.0)
                for _venue, q in routing.items()
                if q >= MIN_SLICE_QTY
            ]
    else:
        # MARKET / 未知 → 单笔
        children = [ChildOrder(qty=parent_qty, delay_ms=0.0)]

    slices = _to_slices(children)
    meta["slices"] = len(slices)
    return slices, meta


def execute_slices(
    children: List[AlgoSlice],
    place_fn: Callable[[float, bool], Any],
    *,
    sleep_fn: Optional[Callable[[float], None]] = None,
    log_prefix: str = "[AlgoExec]",
) -> Dict[str, Any]:
    """按序执行子单。

    Args:
        children: build_algo_slices 产出的子单序列
        place_fn: (child_qty, is_last) -> result（调用方负责具体下单 + TP/SL 策略）
        sleep_fn: 片间等待（默认 time.sleep）
        log_prefix: 日志前缀（含 algo 标识）

    Returns:
        {"results": [...], "errors": [...], "completed": n, "total": n}
    """
    sleep_fn = sleep_fn or time.sleep
    results: List[Any] = []
    errors: List[str] = []
    total = len(children)
    prev_delay_ms = 0.0
    for i, ch in enumerate(children):
        # delay_ms 是相对父单的绝对偏移（cumulative），此处换算为增量等待
        if i > 0 and ch.delay_ms > prev_delay_ms:
            try:
                sleep_fn((ch.delay_ms - prev_delay_ms) / 1000.0)
            except Exception:
                pass
        prev_delay_ms = max(prev_delay_ms, ch.delay_ms)
        try:
            r = place_fn(ch.qty, ch.is_last)
            results.append(r)
        except Exception as e:
            errors.append(f"slice{i}: {e}")
            logger.error(f"{log_prefix} 子单 {i + 1}/{total} 执行异常: {e}", exc_info=True)
    return {
        "results": results,
        "errors": errors,
        "completed": len(results),
        "total": total,
    }
