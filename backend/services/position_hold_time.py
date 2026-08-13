"""三周期持仓时限 — tier 复审点 + AI 可延长 expected_hold_hours。"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple, Union

from backend.services.sub_position_manager import NATURE_TO_TIER, get_rules

PositionLike = Any

# tier 软复审间隔之上，AI 最多可延长到 soft_cap × 倍数（仅 mid/long）
TIER_HOLD_EXTENSION_MAX_MULT = float(os.getenv("TIER_HOLD_EXTENSION_MAX_MULT", "3"))
# AI 单次延长建议区间（小时），与 trading_analysts prompt 一致
AI_EXTEND_HOLD_HOURS_MIN = float(os.getenv("AI_EXTEND_HOLD_HOURS_MIN", "4"))
AI_EXTEND_HOLD_HOURS_MAX = float(os.getenv("AI_EXTEND_HOLD_HOURS_MAX", "16"))

# 无 AI 复审/延长车道：短线（scalp/intraday）超时规则强平；
# 研究车道（pair_research/research）与 mid 完全隔离，2h 固定上限同样规则强平
_SHORT_NO_AI_HOLD_NATURES = frozenset({"scalp", "intraday", "pair_research", "research"})


def is_short_no_ai_hold_nature(nature: str | None) -> bool:
    """短线仓：不走 Master 持仓时限复审/延长，由 TP/SL + 硬超时管理。"""
    return str(nature or "").strip().lower() in _SHORT_NO_AI_HOLD_NATURES


def resolve_nature_from_position(pos: PositionLike) -> str:
    nature = (getattr(pos, "trade_nature", None) or "").strip().lower()
    if isinstance(pos, dict) and not nature:
        nature = (pos.get("trade_nature") or "").strip().lower()
    return nature


def resolve_tier_from_position(pos: PositionLike) -> str:
    nature = resolve_nature_from_position(pos)
    if nature:
        return NATURE_TO_TIER.get(nature, getattr(pos, "timeframe_tier", None) or "mid")
    tier = (getattr(pos, "timeframe_tier", None) or "mid").strip().lower()
    if isinstance(pos, dict) and tier == "mid":
        tier = (pos.get("timeframe_tier") or "mid").strip().lower()
    return tier if tier in ("short", "mid", "long") else "mid"


def _position_expected_hold_hours_raw(pos: PositionLike) -> Optional[float]:
    eh = getattr(pos, "expected_hold_hours", None)
    if eh is None and isinstance(pos, dict):
        eh = pos.get("expected_hold_hours")
    if eh is not None and float(eh) > 0:
        return float(eh)
    return None


def resolve_tier_review_seconds(pos: PositionLike) -> int:
    """tier 复审检查点（如中线 8h）— 到达后触发 AI 评估，不等于强平。"""
    tier = resolve_tier_from_position(pos)
    try:
        from backend.services.runtime_tuning_store import get_tier_value
        from backend.services.paper_pace_controller import paper_pace_controller
        from backend.config.settings import TIER_PROTECTION_PARAMS

        default = int(TIER_PROTECTION_PARAMS.get(tier, {}).get("max_hold_sec", 0) or 0)
        base = int(get_tier_value("tier_max_hold_sec", tier, float(default)))
        # [三周期持仓时间收敛 2026-08-13] 短线/研究车道复审点固定（pace 节奏倍率
        # 不生效），否则同一仓时限随 gear 在 0.85x~1.8x 漂移；mid/long 保留 pace 倍率。
        if tier in ("short", "research"):
            mult = 1.0
        else:
            mult = paper_pace_controller.get_knobs().hold_timeout_multiplier
        return max(60, int(base * mult))
    except Exception:
        from backend.config.settings import TIER_PROTECTION_PARAMS
        return int(TIER_PROTECTION_PARAMS.get(tier, {}).get("max_hold_sec", 0) or 0)


def resolve_tier_absolute_cap_seconds(pos: PositionLike) -> int:
    """AI 延长持仓的绝对天花板。

    - scalp/intraday：= 复审点（禁止延长倍数）
    - mid/long：复审点 × TIER_HOLD_EXTENSION_MAX_MULT
    """
    review = resolve_tier_review_seconds(pos)
    if review <= 0:
        return 604800
    nature = resolve_nature_from_position(pos)
    if is_short_no_ai_hold_nature(nature):
        return review
    return int(review * TIER_HOLD_EXTENSION_MAX_MULT)


def resolve_expected_hold_hours(pos: PositionLike) -> float:
    """策略 nature 默认预期持仓（小时）。"""
    nature = resolve_nature_from_position(pos) or "swing"
    rule_h = float(get_rules(nature).get("expected_hold_hours", 24))
    stored = _position_expected_hold_hours_raw(pos)
    if stored is not None:
        tier = resolve_tier_from_position(pos)
        if tier == "long" and stored < rule_h:
            return rule_h
        return stored
    review_h = resolve_tier_review_seconds(pos) / 3600.0
    if review_h > 0:
        return min(rule_h, review_h)
    return rule_h


def resolve_max_hold_seconds(pos: PositionLike) -> int:
    """
    有效持仓上限（秒）= 仓位 expected_hold_hours（AI 可延长），封顶 absolute_cap。
    未显式设置时，默认 = tier 复审点。

    短线 scalp/intraday：强制封顶在复审点（禁止用开仓 8h 规则压过热调 3h）。
    AI 自动选币额外封顶：不超过 AUTO_COIN_MAX_HOLD_SEC（默认 72h）。
    """
    abs_cap = resolve_tier_absolute_cap_seconds(pos)
    review_sec = resolve_tier_review_seconds(pos)
    nature = resolve_nature_from_position(pos)
    pos_h = _position_expected_hold_hours_raw(pos)

    # 短线：无论开仓写了多少小时，有效上限不超过复审点（=绝对天花板）
    if is_short_no_ai_hold_nature(nature):
        if pos_h is not None:
            result = min(int(pos_h * 3600), abs_cap)
        elif review_sec > 0:
            result = review_sec
        else:
            result = abs_cap
    elif pos_h is not None:
        result = min(int(pos_h * 3600), abs_cap)
    elif review_sec > 0:
        result = min(review_sec, abs_cap)
    else:
        nature_sec = int(resolve_expected_hold_hours(pos) * 3600)
        result = min(nature_sec, abs_cap) if nature_sec > 0 else abs_cap

    # AI 自动选币强制封顶：最长持仓不超过 AUTO_COIN_MAX_HOLD_SEC
    try:
        sym = (getattr(pos, "symbol", "") or "").strip().upper()
        if isinstance(pos, dict):
            sym = sym or (pos.get("symbol") or "").strip().upper()
        if sym:
            from backend.services.auto_coin_selector import is_auto_coin_symbol
            from backend.config.settings import AUTO_COIN_MAX_HOLD_SEC
            if is_auto_coin_symbol(sym):
                result = min(result, AUTO_COIN_MAX_HOLD_SEC)
    except ImportError:
        pass

    return result


def position_opened_at_utc(pos: PositionLike) -> Optional[datetime]:
    opened = getattr(pos, "opened_at", None)
    if opened is None and isinstance(pos, dict):
        opened = pos.get("opened_at")
    if not opened:
        return None
    from backend.utils.db_datetime import parse_db_naive_to_utc
    return parse_db_naive_to_utc(opened)


def position_hold_age_seconds(pos: PositionLike) -> float:
    opened = position_opened_at_utc(pos)
    if not opened:
        return 0.0
    return max(0.0, (datetime.now(timezone.utc) - opened).total_seconds())


def get_position_hold_status(pos: PositionLike) -> Dict[str, Any]:
    max_sec = resolve_max_hold_seconds(pos)
    review_sec = resolve_tier_review_seconds(pos)
    age_sec = position_hold_age_seconds(pos)
    tier = resolve_tier_from_position(pos)
    nature = resolve_nature_from_position(pos)
    expected_h = resolve_expected_hold_hours(pos)
    max_h = max_sec / 3600.0 if max_sec > 0 else 0.0
    review_h = review_sec / 3600.0 if review_sec > 0 else max_h
    age_h = age_sec / 3600.0
    remaining_h = max(0.0, max_h - age_h) if max_h > 0 else None
    progress = (age_sec / max_sec) if max_sec > 0 else 0.0
    expired = max_sec > 0 and age_sec > max_sec

    # 短线：不展示「待AI复审」（无 AI 持仓复审路径）
    short_no_ai = is_short_no_ai_hold_nature(nature)
    review_threshold = review_sec * 0.85 if review_sec > 0 else max_sec * 0.85
    near_timeout = (
        not short_no_ai
        and not expired
        and review_sec > 0
        and age_sec >= review_threshold
        and age_sec <= max_sec
    )

    # 「AI已延长」：仅 mid/long，且当前上限明确超过开仓初始值（避免 nature=8h > review=3h 假阳性）
    initial_h = resolve_initial_expected_hold_hours(
        trade_nature=nature or "swing",
        timeframe_tier=tier,
    )
    stored_h = _position_expected_hold_hours_raw(pos)
    ai_extended = (
        not short_no_ai
        and stored_h is not None
        and stored_h > initial_h + 0.05
        and review_sec > 0
        and max_sec > review_sec
    )

    abs_cap_sec = resolve_tier_absolute_cap_seconds(pos)
    abs_cap_h = abs_cap_sec / 3600.0 if abs_cap_sec > 0 else max_h
    extendable_h = 0.0 if short_no_ai else (
        max(0.0, abs_cap_h - max_h) if abs_cap_h > 0 else 0.0
    )
    return {
        "tier": tier,
        "trade_nature": nature,
        "expected_hold_hours": round(expected_h, 2),
        "max_hold_hours": round(max_h, 2),
        "review_hold_hours": round(review_h, 2),
        "absolute_cap_hours": round(abs_cap_h, 2),
        "extendable_hours": round(extendable_h, 2),
        "extend_step_hours_min": AI_EXTEND_HOLD_HOURS_MIN,
        "extend_step_hours_max": AI_EXTEND_HOLD_HOURS_MAX,
        "hold_age_hours": round(age_h, 2),
        "hold_remaining_hours": round(remaining_h, 2) if remaining_h is not None else None,
        "hold_progress_pct": round(min(100.0, progress * 100), 1),
        "hold_expired": expired,
        "hold_near_timeout": near_timeout,
        "hold_ai_extended": ai_extended,
        "hold_ai_reviewable": not short_no_ai,
        "max_hold_sec": max_sec,
        "review_hold_sec": review_sec,
        "hold_age_sec": int(age_sec),
    }


def is_position_hold_expired(pos: PositionLike) -> Tuple[bool, Dict[str, Any]]:
    status = get_position_hold_status(pos)
    return bool(status.get("hold_expired")), status


def resolve_initial_expected_hold_hours(
    trade_nature: str = "swing",
    timeframe_tier: Optional[str] = None,
) -> float:
    """开仓时初始持仓上限 = min(nature 预期, tier 复审点[含热调/节奏])。

    与 resolve_tier_review_seconds 同源，避免开仓写 8h、复审却是 3h 的假「AI已延长」。
    """
    from backend.services.sub_position_manager import NATURE_TO_TIER, get_rules

    nature = (trade_nature or "swing").strip().lower()
    tier = (timeframe_tier or NATURE_TO_TIER.get(nature, "mid")).strip().lower()

    # 构造最小 pos 以复用 runtime review 计算
    class _P:
        trade_nature = nature
        timeframe_tier = tier
        expected_hold_hours = None

    review_h = resolve_tier_review_seconds(_P()) / 3600.0
    nature_h = float(get_rules(nature).get("expected_hold_hours", 24))

    # 短线进一步收紧：不超过复审点（且 nature 规则若更大也砍掉）
    if is_short_no_ai_hold_nature(nature):
        if review_h > 0:
            return min(nature_h, review_h)
        return min(nature_h, 3.0)

    if review_h > 0:
        return min(nature_h, review_h)
    return nature_h


def format_hold_timeout_reason(status: Dict[str, Any], symbol: str = "") -> str:
    tier = status.get("tier", "mid")
    label = {"short": "短线", "mid": "中线", "long": "长线"}.get(tier, tier)
    age_h = status.get("hold_age_hours", 0)
    max_h = status.get("max_hold_hours", 0)
    review_h = status.get("review_hold_hours", max_h)
    sym = f"{symbol} " if symbol else ""
    if status.get("hold_expired"):
        if status.get("hold_ai_reviewable") is False:
            return (
                f"{sym}{label}硬超时: 已持{age_h:.1f}h > 上限{max_h:.1f}h（禁AI延长，规则强平）"
            )
        return (
            f"{sym}{label}持仓超过 AI 批准上限: 已持{age_h:.1f}h > {max_h:.1f}h"
        )
    return (
        f"{sym}{label}到达复审点: 已持{age_h:.1f}h / 复审{review_h:.1f}h "
        f"(当前上限{max_h:.1f}h，需 AI 决定平仓或延长)"
    )
