"""Deterministic quant signals (85% weight)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.services.mlto.types import PerceptionPacket, Signal, ThesisDTO


def _bias_value(bias: str) -> float:
    b = (bias or "neutral").lower()
    if b in ("bullish", "long", "buy"):
        return 1.0
    if b in ("bearish", "short", "sell"):
        return 0.0
    return 0.5


def compute(packet: PerceptionPacket, thesis: ThesisDTO, db=None) -> List[Signal]:
    signals: List[Signal] = []
    orch = packet.orchestrator or {}
    qb = packet.quant_brief or {}
    tier = packet.tier

    if tier == "mid":
        conf = float(orch.get("mid_confidence") or orch.get("mid_conf") or 0)
        signals.append(
            Signal("orch_mid_bias", _bias_value(orch.get("mid_bias")), min(1.0, conf + 0.2), "framework")
        )
    else:
        conf = float(orch.get("long_confidence") or orch.get("long_conf") or 0)
        signals.append(
            Signal("orch_long_bias", _bias_value(orch.get("long_bias")), min(1.0, conf + 0.2), "framework")
        )

    align = int(qb.get("alignment_score") or 0)
    signals.append(Signal("quant_alignment", min(1.0, align / 15.0), 0.9, "framework"))

    timing = _entry_timing(packet.market_summary_sym)
    signals.append(Signal("entry_timing", timing, 0.9, "framework"))

    health = _thesis_health(db, packet.symbol, tier)
    signals.append(Signal("thesis_health", health, 0.85, "framework"))

    consensus = _analyst_consensus(packet.analyst_reports, packet.symbol)
    signals.append(Signal("analyst_consensus", consensus, 0.7, "framework"))

    fb = _feedback_loop(thesis)
    signals.append(Signal("feedback_loop", fb, 0.8, "framework"))

    # [P5-修复] LLM 方向语义化映射：llm_conviction 的 clamp(0,100) 使 0 与
    # "从未评级"不可区分——LLM 持续 neutral 时 conviction_delta=0 → conviction 恒 0，
    # 但 review_count 每次 LLM 调用都 +1（含失败空响应），旧判据
    # `review_count<=0 and llm_conviction==0` 永假 → 中性被映射成 0.0（极度看空）
    # → ai_governed 下 direction 恒 short、composite 被拉低 → 长线 0 开仓。
    # 现在以 thesis.direction（orchestrator 在 quant 之前已写入 LLM 最新方向）为
    # 真语义：neutral → 0.5（不贡献方向，落入 _orch_bias_direction 兜底）；
    # long/short → 映射后夹取到方向侧，conviction=0 不再反噬方向。
    # 夹取边界 0.55/0.45 与 decision_hub._derive_direction 的 ai_governed 映射精确对齐。
    _d = (thesis.direction or "neutral").lower()
    if _d == "neutral":
        llm_v = 0.5
    else:
        llm_v = 0.5 + (thesis.llm_conviction - 50) / 100.0
        llm_v = max(llm_v, 0.55) if _d == "long" else min(llm_v, 0.45)
    # [阶段3b] confidence 0.5→0.85：让 LLM 全权重（0.30）真正生效。
    # 旧值 0.5 使有效权重=0.30×0.5=0.15（半折），LLM 研判被系统性低估。
    # LLM thesis 是经过推理的研判，0.85 的"信号可靠度"合理（vs 规则信号 0.8-0.9）。
    signals.append(Signal("llm_qual", max(0.0, min(1.0, llm_v)), 0.85, "llm"))

    # [阶段2] 中周期择时信号（来自长线 thesis 嵌入的 mid_view 子结构）。
    # 仅当 mid_view 存在且 timing_score>0 时产出；权重在阶段3 decision_hub 配置。
    # backward-compatible: mid_view=None → 不产出此信号（现状）。
    if thesis.mid_view and thesis.mid_view.timing_score:
        signals.append(
            Signal(
                "mid_timing",
                thesis.mid_view.timing_score / 100.0,
                0.8,
                "framework",
            )
        )

    # M9 中长线因子锚：4h 因子暴露矩阵修正（开关默认关）
    import os as _os
    if _os.getenv("FEATURE_MIDLONG_FACTOR_ANCHOR_ENABLED", "false").lower() in (
        "1", "true", "yes", "on",
    ):
        try:
            from backend.services.factor_engine.exposure_service import (
                factor_exposure_service,
            )
            _exp = factor_exposure_service.exposure(packet.symbol, "4h", 200)
            if _exp:
                _alpha = sum(float(e.get("expected_alpha", 0) or 0) for e in _exp)
                _anchor_v = max(-1.0, min(1.0, _alpha * 20.0))
                signals.append(Signal("factor_anchor_4h", _anchor_v, 0.5, "quant"))
        except Exception:
            pass

    return signals


def _entry_timing(ms: Dict[str, Any]) -> float:
    ind = ms.get("indicators_4h") if isinstance(ms.get("indicators_4h"), dict) else {}
    if not ind or ind.get("rsi") is None:
        ind = ms.get("indicators_1h") if isinstance(ms.get("indicators_1h"), dict) else {}
    rsi = ind.get("rsi")
    if rsi is None:
        return 0.5
    rsi = float(rsi)
    if rsi < 35:
        return 0.85
    if rsi < 45:
        return 0.65
    if rsi > 70:
        return 0.25
    if rsi > 60:
        return 0.4
    return 0.55


def _thesis_health(db, symbol: str, tier: str) -> float:
    if db is None:
        return 0.5
    try:
        from backend.database.models import StrategyTrade
        from backend.services.unified_learning_service import TIER_TO_NATURE
        nature = TIER_TO_NATURE.get(tier, "swing")
        rows = (
            db.query(StrategyTrade)
            .filter(StrategyTrade.symbol == symbol.upper())
            .order_by(StrategyTrade.closed_at.desc().nullslast())
            .limit(20)
            .all()
        )
        if not rows:
            return 0.5
        wins = sum(1 for r in rows if float(r.pnl or 0) > 0)
        return wins / len(rows)
    except Exception:
        return 0.5


def _analyst_consensus(reports: Dict[str, Any], symbol: str) -> float:
    if not isinstance(reports, dict):
        return 0.5
    votes = []
    for key in ("technical", "risk", "macro"):
        rep = reports.get(key)
        if not isinstance(rep, dict):
            continue
        sym_data = rep.get(symbol) or rep.get(symbol.upper()) or rep
        if isinstance(sym_data, dict):
            bias = str(sym_data.get("bias") or sym_data.get("direction") or "neutral").lower()
            votes.append(_bias_value(bias))
        elif isinstance(rep, dict) and rep.get("overall_bias"):
            votes.append(_bias_value(rep.get("overall_bias")))
    if not votes:
        return 0.5
    return sum(votes) / len(votes)


def _feedback_loop(thesis: ThesisDTO) -> float:
    if not thesis.owm_weights:
        return 0.5
    return min(1.0, max(0.0, sum(thesis.owm_weights.values()) / max(len(thesis.owm_weights), 1) / 1.2))
