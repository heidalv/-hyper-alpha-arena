"""MidLongQuantBrief — 零 LLM 量化简报 + alignment_score 门控。"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class MidLongQuantBrief:
    symbol: str
    direction: str = "neutral"
    regime: str = "unknown"
    alignment_score: int = 0
    structure_levels: Dict[str, float] = field(default_factory=dict)
    missing_data: List[str] = field(default_factory=list)
    evidence_available_ratio: float = 0.0
    cited_fact_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MidLongQuantBriefBuilder:
    """仅 pandas/指标，不调 LLM。"""

    _FACT_KEYS = (
        "mid_bias", "mid_confidence", "long_bias", "long_confidence",
        "rsi_1h", "rsi_4h", "ema_trend_1h", "ema_trend_4h",
        "macd_hist_1h", "vol_ratio_1h", "adx_1d", "trend_1w",
        "funding_rate", "oi_change", "fear_greed",
    )

    def build(
        self,
        symbol: str,
        market_data: Dict[str, Any],
        orchestrator: Optional[Dict[str, Any]] = None,
        side_hint: str = "long",
    ) -> MidLongQuantBrief:
        orch = orchestrator if isinstance(orchestrator, dict) else {}
        md = market_data if isinstance(market_data, dict) else {}
        missing: List[str] = []
        score = 0
        cited: List[str] = []
        side_l = (side_hint or "long").lower()

        mid_bias = str(orch.get("mid_bias") or "neutral")
        long_bias = str(orch.get("long_bias") or "neutral")
        mid_conf = float(orch.get("mid_confidence") or orch.get("mid_conf") or 0)
        long_conf = float(orch.get("long_confidence") or orch.get("long_conf") or 0)

        if mid_bias != "neutral":
            score += 2
            cited.append("mid_bias")
        if long_bias != "neutral":
            score += 2
            cited.append("long_bias")
        if mid_conf >= 0.35:
            score += 1
            cited.append("mid_confidence")
        if long_conf >= 0.35:
            score += 1
            cited.append("long_confidence")

        ind_1h = md.get("indicators_1h") if isinstance(md.get("indicators_1h"), dict) else {}
        ind_4h = md.get("indicators_4h") if isinstance(md.get("indicators_4h"), dict) else {}
        ind_1d = md.get("indicators_1d") if isinstance(md.get("indicators_1d"), dict) else {}

        def _bump(key: str, cond: bool, pts: int = 1):
            nonlocal score
            if cond:
                score += pts
                cited.append(key)
            else:
                missing.append(key)

        rsi_1h = ind_1h.get("rsi")
        _bump("rsi_1h", rsi_1h is not None)
        if rsi_1h is not None and side_l in ("long", "buy") and rsi_1h < 70:
            score += 1
        if rsi_1h is not None and side_l in ("short", "sell") and rsi_1h > 30:
            score += 1

        ema_1h = ind_1h.get("ema_trend")
        _bump("ema_trend_1h", bool(ema_1h))
        if ema_1h == "bullish" and side_l in ("long", "buy"):
            score += 1
        if ema_1h == "bearish" and side_l in ("short", "sell"):
            score += 1

        _bump("rsi_4h", ind_4h.get("rsi") is not None)
        _bump("macd_hist_1h", ind_1h.get("macd_hist") is not None)
        _bump("vol_ratio_1h", ind_1h.get("vol_ratio") is not None)
        _bump("adx_1d", md.get("adx_1d") is not None or ind_1d.get("adx") is not None)

        # [2026-07-31] 优先读 indicators_1w.trend/ema_trend；旧逻辑只看 md.trend_1w /
        # ind_1d.trend_1w，注入写了周线块也永远算 missing。
        ind_1w = md.get("indicators_1w") if isinstance(md.get("indicators_1w"), dict) else {}
        trend_1w = (
            md.get("trend_1w")
            or ind_1w.get("trend")
            or ind_1w.get("ema_trend")
            or ind_1d.get("trend_1w")
        )
        _bump("trend_1w", trend_1w is not None)

        fr = md.get("funding_rate")
        _bump("funding_rate", fr is not None)

        fg = md.get("fear_greed") or (md.get("onchain_macro") or {}).get("fear_greed")
        _bump("fear_greed", fg is not None)

        avail = len(cited) / max(len(self._FACT_KEYS), 1)
        direction = "long" if long_bias in ("bullish", "long") else (
            "short" if long_bias in ("bearish", "short") else mid_bias
        )
        regime = str(md.get("market_cycle") or md.get("regime") or "unknown")

        levels = {}
        for k in ("swing_low", "swing_high"):
            v = md.get(k)
            if v:
                levels[k] = float(v)

        return MidLongQuantBrief(
            symbol=symbol,
            direction=direction,
            regime=regime,
            alignment_score=min(15, score),
            structure_levels=levels,
            missing_data=missing,
            evidence_available_ratio=round(avail, 3),
            cited_fact_ids=cited,
        )


mid_long_quant_brief_builder = MidLongQuantBriefBuilder()
