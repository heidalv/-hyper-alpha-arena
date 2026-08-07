"""TrapSoft + MTF confluence 门控。"""
from __future__ import annotations

import logging
import os
from typing import List

from backend.services.coin_rank.score import RankResult

logger = logging.getLogger(__name__)


def gates_enabled() -> bool:
    try:
        from backend.config.settings import COIN_RANK_GATES_ENABLED
        return bool(COIN_RANK_GATES_ENABLED)
    except Exception:
        return os.getenv("COIN_RANK_GATES_ENABLED", "true").lower() in ("1", "true", "yes", "on")


def _trap_soft_reject() -> float:
    try:
        from backend.config.settings import COIN_RANK_TRAP_SOFT_REJECT
        return float(COIN_RANK_TRAP_SOFT_REJECT)
    except Exception:
        return float(os.getenv("COIN_RANK_TRAP_SOFT_REJECT", "0.55"))


def _trap_hard_reject() -> float:
    try:
        from backend.config.settings import COIN_RANK_TRAP_HARD_REJECT
        return float(COIN_RANK_TRAP_HARD_REJECT)
    except Exception:
        return float(os.getenv("COIN_RANK_TRAP_HARD_REJECT", "0.85"))


def _mtf_min_for_strong() -> float:
    try:
        from backend.config.settings import COIN_RANK_MTF_MIN_STRONG
        return float(COIN_RANK_MTF_MIN_STRONG)
    except Exception:
        return float(os.getenv("COIN_RANK_MTF_MIN_STRONG", "0.5"))


def apply_gates(results: List[RankResult]) -> List[RankResult]:
    """原地写 gate；hard_reject 仍保留在列表供观察，由调用方过滤强烈推荐。"""
    if not gates_enabled():
        for r in results:
            r.gate = "pass"
        return results

    soft_th = _trap_soft_reject()
    hard_th = _trap_hard_reject()
    mtf_min = _mtf_min_for_strong()

    for r in results:
        gate = "pass"
        if r.trap_soft >= hard_th:
            gate = "hard_reject"
            r.explain.append("gate:hard_trap")
        elif r.trap_soft >= soft_th:
            gate = "soft_reject"
            r.explain.append("gate:soft_trap")
        elif r.mtf_confluence < mtf_min and r.composite >= 0.45:
            # 多周期打架：不得进强烈推荐（soft）
            gate = "soft_reject"
            r.explain.append("gate:mtf_block_strong")
        r.gate = gate

    n_soft = sum(1 for r in results if r.gate == "soft_reject")
    n_hard = sum(1 for r in results if r.gate == "hard_reject")
    if n_soft or n_hard:
        logger.info("[CoinRank.gates] soft_reject=%d hard_reject=%d", n_soft, n_hard)
    return results


def is_strong_eligible(r: RankResult) -> bool:
    return r.gate == "pass"
