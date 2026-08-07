"""ATAS 因子 → TradeProposal 适配器（ReplayHarness / 回测同管道）。"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def atas_factor_to_proposal(
    symbol: str,
    market_data: dict,
    *,
    tier: str = "mid",
) -> Tuple[Optional[Any], str]:
    """从 ATASFactorCache / factor_v3 生成 TradeProposal 或 (None, reason)。"""
    from backend.services.decision_core.proposal import TradeProposal

    sym = str(symbol).upper()
    mkt = market_data if isinstance(market_data, dict) else {}
    score = 0.0
    direction = "neutral"
    reasoning = ""

    fv3 = mkt.get("factor_v3") or mkt.get("factor_signal") or {}
    if isinstance(fv3, dict):
        direction_f = float(fv3.get("direction") or 0)
        strength = float(fv3.get("strength") or abs(direction_f) or 0)
        score = strength * 100 if strength <= 1 else strength
        if direction_f > 0.15:
            direction = "buy"
        elif direction_f < -0.15:
            direction = "sell"
        reasoning = f"ATAS factor_v3 dir={direction_f:.2f} strength={strength:.2f}"

    if not direction or direction == "neutral":
        composite = mkt.get("atas_composite") or mkt.get("composite_score")
        if composite is not None:
            cs = float(composite)
            score = abs(cs) if abs(cs) > 1 else abs(cs) * 100
            if cs > 0.2:
                direction = "buy"
            elif cs < -0.2:
                direction = "sell"
            reasoning = f"ATAS composite={cs:.2f}"

    if direction not in ("buy", "sell"):
        return None, "atas_no_signal"

    nature_map = {"short": "scalp", "mid": "swing", "long": "trend_follow"}
    proposal = TradeProposal.from_agent(
        sym=sym,
        tier=tier,
        action=direction,
        confidence=max(score, 45.0),
        trade_nature=nature_map.get(tier, "swing"),
        source_lane="atas_replay",
        reasoning=reasoning,
    )
    return proposal, "ok"


def load_atas_market_overlay(symbol: str, db=None) -> Dict[str, Any]:
    """从 ATASFactorCache 读取最新因子写入 market_data  overlay。"""
    sym = str(symbol).upper()
    overlay: Dict[str, Any] = {}
    try:
        if db is None:
            from backend.database.connection import SessionLocal
            db = SessionLocal()
            own = True
        else:
            own = False
        from backend.database.models import ATASFactorCache
        row = (
            db.query(ATASFactorCache)
            .filter(ATASFactorCache.cache_key.like(f"{sym}_%"))
            .order_by(ATASFactorCache.calculated_at.desc())
            .first()
        )
        if row and row.value:
            fd = row.value if isinstance(row.value, dict) else {}
            overlay["factor_v3"] = fd
            overlay["atas_composite"] = fd.get("composite") or fd.get("score")
        if own:
            db.close()
    except Exception as err:
        logger.debug("[ATASProposer] cache 读取跳过: %s", err)
    return overlay
