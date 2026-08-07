"""开仓置信度门槛 — 与编排器分层置信度、行情 regime 联动。"""
from __future__ import annotations

from typing import Any, Dict, Optional


def normalize_conf_fraction(value: Any) -> float:
    """将 0~1 或 0~100 的置信度统一为 0~1。"""
    try:
        x = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return x / 100.0 if x > 1.0 else max(0.0, min(1.0, x))


def resolve_entry_gate_pct(
    tier: str,
    regime: Optional[str] = None,
    orchestrator: Optional[Dict[str, Any]] = None,
) -> int:
    """
    返回该 tier 开仓所需的最低置信度（百分比整数）。

    - 震荡/横盘：基础门槛较低（42%）
    - 趋势：48%
    - 高波动：55%
    - 危机/崩盘：70%
    - 编排器 final_action=enter 时：门槛跟随对应 tier 的编排器置信度（下限 38%，上限 52%）
    """
    orch = orchestrator if isinstance(orchestrator, dict) else {}
    reg = (regime or orch.get("regime") or "unknown").lower()
    tier_key = (tier or "mid").lower()
    conf_field = {"long": "long_conf", "mid": "mid_conf", "short": "short_conf"}.get(
        tier_key, "mid_conf"
    )
    tier_conf = normalize_conf_fraction(orch.get(conf_field, 0))

    if "crisis" in reg or "crash" in reg:
        base = 70
    elif "volatile" in reg:
        base = 55
    elif "ranging" in reg or "range" in reg:
        base = 40    # Fix 2: 回调门槛 35→40。震荡市假突破多，门槛过低导致噪音开仓
    elif "trend" in reg:
        base = 45    # Fix 2: 回调门槛 42→45。趋势市需要更高确认
    else:
        base = 48    # Fix 2: 回调门槛 45→48。未知 regime 用保守门槛

    fin_action = str(orch.get("final_action", "") or "").lower()
    action = str(orch.get("action", "") or "").lower()
    is_enter = fin_action == "enter" or action == "enter"

    if not is_enter:
        if tier_key == "scalp":
            return min(60, base + 8)
        return base

    # 编排器明确建议进场：门槛与对应 tier 置信度对齐（略低 2pt 避免边界卡死）
    linked = int(round(tier_conf * 100)) - 2
    linked = max(30, min(48, linked))   # 混合模式：下限38→30，上限52→48
    if tier_key in ("short",) and "scalp" not in tier_key:
        linked = max(35, linked)        # 混合模式：40→35
    if tier_key == "scalp":
        linked = max(40, min(55, linked + 5))  # 混合模式：45→40

    # enter 时取 regime 基础与编排器联动值的较宽松者
    gate = max(30, min(48, min(base, linked) if tier_conf < 0.45 else min(base, linked + 3)))

    # 长线/趋势仓：更高门槛，要求局势判断清楚再出手
    if tier_key == "long":
        gate = min(78, gate + 12)

    return gate


def format_gate_hint(tier: str, regime: str, orchestrator: Optional[Dict[str, Any]]) -> str:
    gate = resolve_entry_gate_pct(tier, regime, orchestrator)
    return f"{gate}%"
