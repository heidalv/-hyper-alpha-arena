"""编排器 UI 展示层辅助 — 从 monolith 迁出（整改#8 Phase2）。"""
from __future__ import annotations

from typing import Any, Dict, Optional


def normalize_orchestrator_for_ui(info: Dict[str, Any]) -> None:
    """前端读 long_conf，后端存 long_confidence — 展示层对齐。"""
    orch = info.get("orchestrator")
    if not isinstance(orch, dict):
        return
    for tier in ("long", "mid", "short"):
        conf_key = f"{tier}_confidence"
        short_key = f"{tier}_conf"
        bias_key = f"{tier}_bias"
        if orch.get(conf_key) is not None and orch.get(short_key) is None:
            orch[short_key] = orch.get(conf_key)
        if not orch.get(bias_key):
            orch[bias_key] = "neutral"


def tier_confidence_pct(
    *,
    tier: str = "long",
    orch: Optional[dict] = None,
    orch_dec=None,
) -> int:
    """编排器 tier 置信度 → 0–100 整数（decision 日志 / Fix18 桩共用）。"""
    conf = 0.0
    if orch_dec is not None:
        view = getattr(orch_dec, f"{tier}_view", None)
        if view is not None:
            conf = float(getattr(view, "confidence", 0) or 0)
    elif isinstance(orch, dict):
        conf = float(
            orch.get(f"{tier}_confidence")
            or orch.get(f"{tier}_conf")
            or 0
        )
    if conf <= 1.0:
        return max(0, int(round(conf * 100)))
    return max(0, int(round(conf)))


def backfill_dec_confidence_from_orch(
    dec: dict,
    *,
    sym: str,
    market_summary: dict,
    tier: str = "long",
) -> int:
    """Fix18 桩 / QuantBrief 拦截后：用编排器 tier 置信回填，避免前端长期显示 0。"""
    cur = int(dec.get("confidence") or 0)
    if cur > 0:
        return cur
    _ms = (market_summary or {}).get(sym) or {}
    _orch = (_ms.get("orchestrator") if isinstance(_ms, dict) else {}) or {}
    pct = tier_confidence_pct(tier=tier, orch=_orch)
    if pct > 0:
        dec["confidence"] = pct
    return pct


def orch_payload_from_decision(dec) -> dict:
    """OrchestratorSlotSnapshot 契约 — HC / OrchBG / fallback 统一字段。"""
    return {
        "action": dec.final_action,
        "final_action": dec.final_action,
        "side": dec.final_side,
        "position_pct": dec.final_position_pct,
        "leverage": dec.final_leverage,
        "sl_pct": getattr(dec, "final_sl_pct", 0),
        "tp_pct": getattr(dec, "final_tp_pct", 0),
        "direction": dec.allowed_direction,
        "long_bias": dec.long_view.bias,
        "long_confidence": dec.long_view.confidence,
        "long_conf": dec.long_view.confidence,
        "mid_bias": dec.mid_view.bias,
        "mid_confidence": dec.mid_view.confidence,
        "mid_conf": dec.mid_view.confidence,
        "short_bias": dec.short_view.bias,
        "short_confidence": dec.short_view.confidence,
        "short_conf": dec.short_view.confidence,
        "recommended_slots": getattr(dec, "recommended_slots", []) or [],
        "slot_actions": getattr(dec, "slot_actions", {}) or {},
        "slot_reasoning": getattr(dec, "slot_reasoning", {}) or {},
        "recommended_nature": dec.recommended_nature,
        "final_side": dec.final_side,
        "allowed_direction": dec.allowed_direction,
        "reasoning": getattr(dec, "reasoning", ""),
    }
