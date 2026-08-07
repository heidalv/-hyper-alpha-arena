"""Paper 开仓 trade_nature / sub_tier 解析 — 从 monolith 迁出。"""
from __future__ import annotations

import logging
from typing import Callable, Set, Tuple

logger = logging.getLogger(__name__)


def resolve_sub_tier_and_nature(
    *,
    strat,
    decision: dict,
    timeframe_tier: str,
    symbol: str,
    market_scan_cache: dict,
    get_validated_trade_nature: Callable[..., str],
    valid_trade_natures: Set[str],
) -> Tuple[str, str]:
    """解析 trade_nature 与 sub_tier（策略 tier 优先）。"""
    _strat_genome = getattr(strat, "genome", None) or {}
    _strat_tier = getattr(strat, "timeframe_tier", None)
    _trade_nature = get_validated_trade_nature(
        genome=_strat_genome if isinstance(_strat_genome, dict) else {},
        decision=decision or {},
        tier=_strat_tier or timeframe_tier or "mid",
    )
    try:
        _mkt_tn = (market_scan_cache or {}).get(symbol, {})
        if isinstance(_mkt_tn, dict):
            _orch_tn = _mkt_tn.get("orchestrator", {})
            if isinstance(_orch_tn, dict):
                _rec_nature = _orch_tn.get("recommended_nature")
                if _rec_nature and _rec_nature.strip().lower() in valid_trade_natures:
                    _trade_nature = _rec_nature.strip().lower()
    except Exception:
        pass

    from backend.services.sub_position_manager import NATURE_TO_TIER, TIER_TO_NATURE, normalize_nature

    _trade_nature = normalize_nature(_trade_nature)
    _nature_tier = NATURE_TO_TIER.get(_trade_nature)
    if _strat_tier and _strat_tier in ("short", "mid", "long"):
        _sub_tier = _strat_tier
        if _nature_tier and _nature_tier != _sub_tier:
            _corrected = TIER_TO_NATURE.get(_sub_tier, _trade_nature)
            logger.info(
                f"[ExecTrade] nature-tier一致性修正: {_trade_nature!r}({_nature_tier}) "
                f"!= strat_tier({_sub_tier}), 修正为 {_corrected!r}"
            )
            _trade_nature = _corrected
    elif _nature_tier and _nature_tier in ("short", "mid", "long"):
        _sub_tier = _nature_tier
    elif timeframe_tier and timeframe_tier in ("short", "mid", "long"):
        _sub_tier = timeframe_tier
    else:
        _sub_tier = "mid"

    logger.info(
        f"[ExecTrade] strat={getattr(strat, 'name', '?')} "
        f"genome_nature={_strat_genome.get('trade_nature', '')!r} "
        f"decision_nature={decision.get('trade_nature')!r} "
        f"final_nature={_trade_nature!r} -> sub_tier={_sub_tier!r} "
        f"(strat_tier={_strat_tier!r}, nature_tier={_nature_tier!r}, "
        f"timeframe_tier={timeframe_tier!r})"
    )
    return _trade_nature, _sub_tier
