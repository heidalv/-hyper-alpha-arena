# -*- coding: utf-8 -*-
"""通道A —— 数值通道编排：K线 → 特征 → 横截面 z → LTR/IC加权打分。"""
from __future__ import annotations

import logging
from typing import Dict, List

from backend.services.hybrid_scoring import features, kpanel, ltr

logger = logging.getLogger(__name__)


def run(symbols: List[str], period: str | None = None) -> Dict[str, Dict[str, object]]:
    """返回 {sym: {score, rank, backend, features}}。score∈[0,1]，rank 从 1 起（分数降序）。"""
    period = period or "1d"
    syms = [kpanel.norm_sym(s) for s in symbols if s]
    rows = features.live_rows(syms, period=period)
    if not rows:
        logger.warning("[HybridScore.channel_a] 无可算特征的币（%d 入参）", len(syms))
        return {}
    zrows = features.cross_z_rows(rows)
    scored = ltr.score(zrows)
    for s, r in scored.items():
        r["features"] = zrows[s]
    order = sorted(scored, key=lambda s: -float(scored[s]["score"]))
    for i, s in enumerate(order):
        scored[s]["rank"] = i + 1
    logger.info(
        "[HybridScore.channel_a] n=%d backend=%s top3=%s",
        len(scored), scored[order[0]]["backend"] if order else "-",
        [(s, round(float(scored[s]["score"]), 3)) for s in order[:3]] if order else [],
    )
    return scored
