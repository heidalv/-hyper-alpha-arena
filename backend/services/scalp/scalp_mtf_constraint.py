"""ScalpMtfConstraint — 独立 scalp 循环的多周期频率约束（阶段一 1.4）。

背景
====
`multi_timeframe_orchestrator._apply_frequency_constraints` 定义了 H1–H5 硬约束
（4h 与 15m 反向缩仓、4h 强信号覆盖 15m、跨周期冲突≥2 降级 wait 等），但那套
只作用于 AI 主编排链的 `OrchestratorDecision`。**独立 scalp 循环
（`_run_scalp_independent`）完全绕过了它**——短线可以在 4h 明确反向时仍满仓逆势开，
这是短线亏损的根因之一。

本模块提供一个**轻量、无额外网络/重算**的约束器：直接复用 OrchBG 后台已经算好并
缓存进 `market_data["orchestrator"]` 的多周期偏向（long_bias=长周期、mid_bias=4h、
short_bias=15m），对 scalp 方向做与 H1/H2/H5 同源的判定：

- 4h(mid) 与 scalp 方向反向 → 硬冲突（4h 高置信 → 禁开；否则缩仓）。
- 4h 与 15m 反向 → 硬冲突（缩仓）。
- 硬冲突 ≥2 → 直接 hold。

产出 `(hold, size_multiplier, conflicts, reason)`，由调用方强制执行。
flag 门控（`SCALP_MTF_ENFORCE_ENABLED`），默认开启，可秒回滚。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class MtfConstraintResult:
    hold: bool = False
    size_multiplier: float = 1.0
    conflicts: List[str] = field(default_factory=list)
    reason: str = ""


def _dir_to_bias(direction: str) -> str:
    d = (direction or "").lower()
    if d in ("long", "buy", "bullish"):
        return "bullish"
    if d in ("short", "sell", "bearish"):
        return "bearish"
    return "neutral"


def _cfg(name: str, default):
    from backend.config import settings as _s
    return getattr(_s, name, default)


def evaluate_scalp_mtf_constraint(
    symbol: str,
    scalp_direction: str,
    market_data: Optional[Dict[str, Any]],
) -> MtfConstraintResult:
    """对 scalp 方向施加多周期约束（复用 OrchBG 缓存的多周期偏向）。

    Args:
        symbol: 交易对
        scalp_direction: scalp 意图方向 long/short
        market_data: 需含 `orchestrator` 字段（OrchBG 写入的多周期偏向）

    Returns:
        MtfConstraintResult
    """
    if not bool(_cfg("SCALP_MTF_ENFORCE_ENABLED", True)):
        return MtfConstraintResult(reason="mtf_enforce_disabled")

    scalp_bias = _dir_to_bias(scalp_direction)
    if scalp_bias == "neutral":
        return MtfConstraintResult(reason="方向中性，无需约束")

    orch = {}
    if isinstance(market_data, dict):
        orch = market_data.get("orchestrator") or {}

    conflicts: List[str] = []
    size_mult = 1.0
    hold = False

    if not isinstance(orch, dict) or not orch:
        # OrchBG 粗粒度多周期偏向缺失时，A/B/C 硬约束无法评估（不能因为数据缺失就
        # 一刀切禁开），但下面独立计算的三重屏幕共振评分不依赖 orchestrator，仍会执行。
        # 注意：这里不写入 conflicts（避免orch常缺失时刷屏 info 日志），仅影响展示变量。
        mid_bias = short_bias = "neutral"
    else:
        mid_bias = str(orch.get("mid_bias") or "neutral").lower()      # 4h
        short_bias = str(orch.get("short_bias") or "neutral").lower()  # 15m
        try:
            mid_conf = float(orch.get("mid_confidence") or orch.get("mid_conf") or 0.0)
        except (TypeError, ValueError):
            mid_conf = 0.0

        strong_conf = float(_cfg("SCALP_MTF_STRONG_CONF", 0.7) or 0.7)
        conflict_mult = float(_cfg("SCALP_MTF_CONFLICT_MULT", 0.5) or 0.5)

        # 2026-07-09 短线逆势解禁 + HARD_ONLY_ANCHOR：默认冲突只缩仓；
        # 仅当未开逆势解禁且 4h 强反向时才 hold（硬锚点）。
        allow_counter = bool(_cfg("SCALP_ALLOW_COUNTER_TREND", True))
        hard_only_anchor = bool(_cfg("SCALP_MTF_HARD_ONLY_ANCHOR", True))
        counter_trend_mult = float(_cfg("SCALP_COUNTER_TREND_SIZE_MULT", 0.5) or 0.5)

        # 约束 A：4h 与 scalp 方向反向
        if mid_bias != "neutral" and mid_bias != scalp_bias:
            if mid_conf >= strong_conf:
                if allow_counter or hard_only_anchor:
                    size_mult *= counter_trend_mult
                    conflicts.append(
                        f"4h强反向({mid_bias},conf={mid_conf:.2f})→逆势缩仓×{counter_trend_mult:.2f}"
                    )
                else:
                    hold = True
                    conflicts.append(f"4h强反向({mid_bias},conf={mid_conf:.2f})→禁开")
            else:
                size_mult *= conflict_mult
                conflicts.append(f"4h反向({mid_bias})→缩仓×{conflict_mult:.2f}")

        # 约束 B：4h 与 15m 反向（H1）
        if (
            mid_bias != "neutral"
            and short_bias != "neutral"
            and mid_bias != short_bias
        ):
            size_mult *= conflict_mult
            conflicts.append(f"4h({mid_bias})≠15m({short_bias})→缩仓×{conflict_mult:.2f}")

        # 约束 C（H5）：硬冲突 ≥2 → 默认直接 hold；逆势解禁/硬锚点模式改为再缩仓放行。
        hard_conflicts = len(conflicts)
        if hard_conflicts >= 2:
            if allow_counter or hard_only_anchor:
                size_mult *= counter_trend_mult
                conflicts.append(f"跨周期冲突≥2→逆势缩仓×{counter_trend_mult:.2f}")
            else:
                hold = True
                conflicts.append("跨周期冲突≥2→hold")

    # 2026-07-18(P2)：叠加三重屏幕加权共振评分
    if not hold:
        try:
            from backend.services.scalp.mtf_resonance_engine import compute_mtf_resonance
            reso = compute_mtf_resonance(symbol, scalp_direction, market_data)
            if reso.available:
                _hard_only = bool(_cfg("SCALP_MTF_HARD_ONLY_ANCHOR", True))
                if reso.no_trade and not _hard_only:
                    hold = True
                    conflicts.append(f"三重屏幕共振:{reso.reason}")
                elif reso.no_trade and _hard_only:
                    size_mult *= 0.35
                    conflicts.append(f"三重屏幕共振→缩仓×0.35({reso.reason})")
                elif reso.size_multiplier != 1.0:
                    size_mult *= reso.size_multiplier
                    conflicts.append(f"三重屏幕共振×{reso.size_multiplier:.2f}({reso.reason})")
        except Exception as e:
            logger.debug(f"[ScalpMTF] {symbol} 三重屏幕共振计算跳过: {e}")

    reason = "; ".join(conflicts) if conflicts else "多周期无冲突"
    if conflicts:
        logger.info(
            "[ScalpMTF] %s scalp=%s mid(4h)=%s short(15m)=%s → hold=%s size×%.2f (%s)",
            symbol, scalp_bias, mid_bias, short_bias, hold, size_mult, reason,
        )

    return MtfConstraintResult(
        hold=hold,
        size_multiplier=max(0.0, size_mult),
        conflicts=conflicts,
        reason=reason,
    )
