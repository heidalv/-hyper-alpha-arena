"""CoinRankEngine 统一入口。"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from backend.services.coin_rank.features import load_dc_ticker_rows, list_universe_symbols, norm_sym
from backend.services.coin_rank.gates import apply_gates
from backend.services.coin_rank.score import RankResult, score_rows

logger = logging.getLogger(__name__)


def engine_enabled() -> bool:
    try:
        from backend.config.settings import COIN_RANK_ENGINE_ENABLED
        return bool(COIN_RANK_ENGINE_ENABLED)
    except Exception:
        return os.getenv("COIN_RANK_ENGINE_ENABLED", "true").lower() in ("1", "true", "yes", "on")


def rank_universe(
    *,
    limit: int = 40,
    apply_factor: bool = True,
    apply_gate: bool = True,
    apply_decay: bool = True,
) -> List[RankResult]:
    """全宇宙粗分 TopN（平台看板主路径）。"""
    rows = load_dc_ticker_rows()
    decay_map = None
    hist_map = None
    if apply_decay:
        try:
            from backend.services.coin_rank.feedback import get_decay_map, get_hist_map

            decay_map = get_decay_map()
            hist_map = get_hist_map()
        except Exception as e:
            logger.debug("[CoinRank] decay skip: %s", e)

    # 先按流动性取更大池再打分截断
    pre = list_universe_symbols(limit=max(limit * 3, 60))
    scored = score_rows(
        rows,
        symbols=pre,
        apply_factor=apply_factor,
        decay_map=decay_map,
        hist_map=hist_map,
    )
    if apply_gate:
        scored = apply_gates(scored)
    return scored[:limit]


def rank_symbols(
    symbols: List[str],
    *,
    apply_factor: bool = True,
    apply_gate: bool = True,
    apply_decay: bool = True,
) -> List[RankResult]:
    """对指定币打分（会话 focus / 轻量车道）。"""
    rows = load_dc_ticker_rows()
    decay_map = None
    hist_map = None
    if apply_decay:
        try:
            from backend.services.coin_rank.feedback import get_decay_map, get_hist_map

            decay_map = get_decay_map()
            hist_map = get_hist_map()
        except Exception:
            pass
    scored = score_rows(
        rows,
        symbols=symbols,
        apply_factor=apply_factor,
        decay_map=decay_map,
        hist_map=hist_map,
    )
    if apply_gate:
        scored = apply_gates(scored)
    return scored


def rank_results_to_platform_candidates(results: List[RankResult]) -> List[Dict[str, Any]]:
    """转成平台 `_scan_market_candidates` 兼容结构。"""
    out = []
    for r in results:
        d = r.to_dict()
        d["market_scores"] = {
            "liquidity": r.liquidity,
            "cs_momentum": r.cs_momentum,
            "ts_momentum": r.ts_momentum,
            "trap_soft": r.trap_soft,
            "mtf_confluence": r.mtf_confluence,
            "gate": r.gate,
            "explain": r.explain,
        }
        out.append(d)
    return out


# re-export
__all__ = [
    "RankResult",
    "engine_enabled",
    "rank_universe",
    "rank_symbols",
    "rank_results_to_platform_candidates",
    "norm_sym",
]
