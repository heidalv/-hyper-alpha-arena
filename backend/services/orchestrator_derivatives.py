"""OrchBG 衍生品轻量指标注入 orchestrator 块。"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def enrich_orchestrator_derivatives(
    symbol: str,
    market_data: Optional[dict],
) -> Dict[str, Any]:
    """从 unified snapshot 提取 OI/funding/CVD 标量，供 mid/long light_context。"""
    if not isinstance(market_data, dict):
        return {}

    sym = str(symbol).upper()
    out: Dict[str, Any] = {}

    funding = float(
        market_data.get("funding_rate")
        or market_data.get("derivatives", {}).get("funding_rate")
        or 0
    )
    oi = float(
        market_data.get("open_interest")
        or market_data.get("derivatives", {}).get("oi")
        or market_data.get("oi")
        or 0
    )
    oi_change = float(
        market_data.get("oi_change_1h_pct")
        or market_data.get("derivatives", {}).get("oi_change_1h_pct")
        or 0
    )
    cvd_slope = float(
        market_data.get("cvd_slope")
        or market_data.get("derivatives", {}).get("cvd_slope")
        or 0
    )
    price = float(market_data.get("price") or market_data.get("current_price") or 0)
    change_1h = float(market_data.get("change_1h_pct") or market_data.get("change_pct") or 0)

    out["funding_rate"] = funding
    out["open_interest"] = oi
    out["oi_change_1h_pct"] = oi_change
    out["cvd_slope"] = cvd_slope

    # 简化背离：价涨但 CVD 斜率负
    if change_1h > 0.3 and cvd_slope < 0:
        out["cvd_divergence"] = "bearish"
    elif change_1h < -0.3 and cvd_slope > 0:
        out["cvd_divergence"] = "bullish"
    else:
        out["cvd_divergence"] = "none"

    # 猎杀倾向：OI 急降 + 大幅波动
    if oi_change < -2.0 and abs(change_1h) > 1.0:
        out["liquidation_bias"] = "long_squeeze" if change_1h > 0 else "short_squeeze"
    else:
        out["liquidation_bias"] = "neutral"

    if funding > 0.0003:
        out["funding_zscore_hint"] = "crowded_long"
    elif funding < -0.0001:
        out["funding_zscore_hint"] = "crowded_short"
    else:
        out["funding_zscore_hint"] = "neutral"

    return out


def inject_derivatives_into_market_summary(market_summary: dict, symbol: str) -> None:
    """就地写入 market_summary[sym]['orchestrator']['derivatives']。"""
    sym = str(symbol).upper()
    if not isinstance(market_summary, dict):
        return
    ms = market_summary.get(sym) or market_summary.get(symbol)
    if not isinstance(ms, dict):
        ms = {}
        market_summary[sym] = ms
    deriv = enrich_orchestrator_derivatives(sym, ms)
    if not deriv:
        return
    orch = ms.get("orchestrator")
    if not isinstance(orch, dict):
        orch = {}
        ms["orchestrator"] = orch
    orch["derivatives"] = deriv
