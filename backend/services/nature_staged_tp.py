"""Nature-aware staged TP + final trailing.

Generic replacement for long_tier_staged_tp. State is serializable so callers can
persist it in paper_positions.exit_state_json.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


@dataclass
class NatureStagedTpState:
    triggered_stages: list[int] = field(default_factory=list)
    peak_pnl_pct: float = 0.0
    trailing_active: bool = False
    trailing_sl_price: Optional[float] = None
    entry_price: float = 0.0
    # S2-5 新增：LLM 的 tp_stages 覆盖（从 exit_plan 读取，持久化到 exit_state_json）
    tp_stages_override: Optional[list] = None
    # S2-5 新增：LLM 的 trailing ATR 倍数覆盖（从 exit_plan 读取）
    trailing_atr_mult_override: Optional[float] = None

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "NatureStagedTpState":
        if not isinstance(data, dict):
            return cls()
        return cls(
            triggered_stages=[int(x) for x in data.get("triggered_stages", [])],
            peak_pnl_pct=float(data.get("peak_pnl_pct", 0.0) or 0.0),
            trailing_active=bool(data.get("trailing_active", False)),
            trailing_sl_price=data.get("trailing_sl_price"),
            entry_price=float(data.get("entry_price", 0.0) or 0.0),
            tp_stages_override=data.get("tp_stages_override"),
            trailing_atr_mult_override=data.get("trailing_atr_mult_override"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class NatureStagedTpDecision:
    action: str
    reason: str = ""
    stage_idx: Optional[int] = None
    reduce_ratio: float = 0.0
    suggested_sl_price: Optional[float] = None
    state: Optional[NatureStagedTpState] = None


def pnl_pct(entry_price: float, current_price: float, side: str) -> float:
    if entry_price <= 0 or current_price <= 0:
        return 0.0
    if str(side).lower() in ("long", "buy"):
        return (current_price - entry_price) / entry_price
    return (entry_price - current_price) / entry_price


def check(
    *,
    entry_price: float,
    current_price: float,
    side: str,
    trade_nature: str,
    atr_pct: float,
    state: NatureStagedTpState,
) -> NatureStagedTpDecision:
    from backend.config.settings import NATURE_EXIT_PROFILES

    profile = NATURE_EXIT_PROFILES.get(trade_nature or "swing", NATURE_EXIT_PROFILES["swing"])

    # S2-5：LLM tp_stages 覆盖默认 NATURE_EXIT_PROFILES.stages
    # LLM 的 tp_stages 格式: [{"pct": 0.06, "close_ratio": 0.30}, ...]
    # 转换为 NATURE_EXIT_PROFILES.stages 格式: [{"trigger_pnl_pct": 0.06, "exit_ratio": 0.30}, ...]
    if state.tp_stages_override and isinstance(state.tp_stages_override, list):
        stages = []
        for s in state.tp_stages_override:
            if isinstance(s, dict):
                try:
                    # LLM 给的是小数(0.06)，NATURE_EXIT_PROFILES 用的也是小数
                    trigger = float(s.get("pct") or s.get("trigger_pnl_pct") or 0)
                    ratio = float(s.get("close_ratio") or s.get("exit_ratio") or 0)
                    if trigger > 0 and ratio > 0:
                        stages.append({"trigger_pnl_pct": trigger, "exit_ratio": ratio})
                except Exception:
                    continue
        if not stages:
            stages = profile.get("stages", [])
    else:
        stages = profile.get("stages", [])

    # S2-5：LLM trailing ATR 倍数覆盖
    trailing_cfg = profile.get("trailing_final", {})
    if state.trailing_atr_mult_override and float(state.trailing_atr_mult_override) > 0:
        trailing_cfg = {**trailing_cfg, "atr_mult": float(state.trailing_atr_mult_override)}

    p = pnl_pct(entry_price, current_price, side)

    if state.entry_price > 0 and abs(entry_price - state.entry_price) > 1e-8:
        state.triggered_stages.clear()
        state.peak_pnl_pct = 0.0
        state.trailing_active = False
        state.trailing_sl_price = None
    state.entry_price = entry_price
    if p > state.peak_pnl_pct:
        state.peak_pnl_pct = p

    for idx, stage in enumerate(stages):
        if idx in state.triggered_stages:
            continue
        trigger = float(stage.get("trigger_pnl_pct", 0) or 0)
        if p >= trigger:
            state.triggered_stages.append(idx)
            return NatureStagedTpDecision(
                action="reduce",
                stage_idx=idx,
                reduce_ratio=float(stage.get("exit_ratio", 0) or 0),
                reason=f"{trade_nature}_tp_stage_{idx + 1}: pnl={p:.3f}>=trigger={trigger:.3f}"
                       + (" [LLM_override]" if state.tp_stages_override else ""),
                state=state,
            )

    if stages and len(state.triggered_stages) >= len(stages):
        state.trailing_active = True
        atr_mult = float(trailing_cfg.get("atr_mult", 2.0) or 2.0)
        min_band = float(trailing_cfg.get("min_band_pct", 0.003) or 0.003)
        band = max(min_band, float(atr_pct or 0) * atr_mult)
        peak_price = entry_price * (1 + state.peak_pnl_pct) if str(side).lower() in ("long", "buy") else entry_price * (1 - state.peak_pnl_pct)
        if str(side).lower() in ("long", "buy"):
            new_sl = peak_price * (1 - band)
            state.trailing_sl_price = round(new_sl, 6)
            if current_price <= new_sl:
                return NatureStagedTpDecision(action="trailing_hit", reason="nature_trailing_hit", state=state)
        else:
            new_sl = peak_price * (1 + band)
            state.trailing_sl_price = round(new_sl, 6)
            if current_price >= new_sl:
                return NatureStagedTpDecision(action="trailing_hit", reason="nature_trailing_hit", state=state)
        return NatureStagedTpDecision(
            action="trailing_update",
            suggested_sl_price=state.trailing_sl_price,
            reason=f"nature_trailing_update peak={state.peak_pnl_pct:.3f}" +
                   (" [LLM_override]" if state.trailing_atr_mult_override else ""),
            state=state,
        )

    return NatureStagedTpDecision(action="hold", reason=f"pnl={p:.3f}", state=state)
