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


def _graph_map_for(symbols: List[str]) -> Optional[Dict[str, Dict[str, float]]]:
    """图信号试点：未启用或计算失败时返回 None（零影响主排序链）。"""
    try:
        from backend.services.coin_rank.graph_signal import compute_graph_signals, graph_signal_enabled

        if not graph_signal_enabled():
            return None
        return compute_graph_signals(symbols) or None
    except Exception as e:
        logger.debug("[CoinRank] graph signal skip: %s", e)
        return None


def log_graph_overlap(results: List[RankResult], graph_map: Optional[Dict[str, Dict[str, float]]]) -> None:
    """验收指标：图信号对排序的影响度（Top-N 重叠率，开 vs 关）。

    用现有输出解析还原「无图信号 composite」（base_old = (composite/decay − w·graph_comp)/(1−w)
    再 clip01，与 score_rows 的融合公式互为逆运算，零额外打分成本），
    计算 Top10/Top20 重叠率并记录日志——设计文档 §验收口径①的自动仪表。
    """
    if not results or not graph_map:
        return
    from backend.services.coin_rank.score import _graph_weights

    gw, lw = _graph_weights()
    if gw <= 0:
        return
    def _clip01(x: float) -> float:
        return max(0.0, min(1.0, float(x)))

    def _no_graph_composite(r: RankResult) -> float:
        if r.graph_score is None:
            return r.composite
        decay = r.decay_mult if r.decay_mult > 0 else 1.0
        base = r.composite / decay
        base_old = (base - gw * r.graph_score) / (1.0 - gw)
        return _clip01(base_old * decay)

    with_graph = [r.symbol for r in results]
    no_graph = sorted(results, key=_no_graph_composite, reverse=True)
    no_graph_syms = [r.symbol for r in no_graph]
    for top_n in (10, 20):
        n = min(top_n, len(results))
        a = set(with_graph[:n])
        b = set(no_graph_syms[:n])
        overlap = len(a & b) / n if n else 1.0
        logger.info("[CoinRank.metrics] graph_overlap top%d=%.2f (graph_weight=%.2f)", n, overlap, gw)


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
    graph_map = _graph_map_for(pre)
    scored = score_rows(
        rows,
        symbols=pre,
        apply_factor=apply_factor,
        decay_map=decay_map,
        hist_map=hist_map,
        graph_map=graph_map,
    )
    log_graph_overlap(scored, graph_map)
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
    graph_map = _graph_map_for(list(symbols))
    scored = score_rows(
        rows,
        symbols=symbols,
        apply_factor=apply_factor,
        decay_map=decay_map,
        hist_map=hist_map,
        graph_map=graph_map,
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
