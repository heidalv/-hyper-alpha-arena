"""
LLM 深度分析路由 — 综合所有新维度（形态、共振、成交量异动、支撑阻力）
"""

from fastapi import APIRouter, Query, HTTPException
from typing import List, Optional
from pydantic import BaseModel, Field
import logging

from backend.services.kline_data_service import kline_service
from backend.services.kline_cache_service import kline_cache
from backend.services.candlestick_pattern_service import detect_patterns
from backend.services.mtf_resonance_service import compute_resonance
from backend.services.support_resistance_service import calculate_support_resistance
from backend.services.volume_anomaly_service import volume_detector

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/klines", tags=["LLM 深度分析"])


class ComprehensiveAnalysisRequest(BaseModel):
    symbol: str
    period: str = "1h"
    kline_count: int = Field(default=100, ge=20, le=500)


class ComprehensiveAnalysisResponse(BaseModel):
    symbol: str
    period: str
    current_price: float

    # 形态
    pattern_count: int
    latest_patterns: List[dict]

    # 共振
    resonance_score: float
    resonance_level: str

    # 成交量异动
    volume_anomaly_count: int
    latest_volume_events: List[dict]

    # 支撑阻力
    nearest_support: Optional[float]
    nearest_resistance: Optional[float]
    pivot: float

    # 摘要
    summary: str
    signals: List[str]


@router.get("/comprehensive/{symbol}", response_model=ComprehensiveAnalysisResponse)
async def comprehensive_analysis(
    symbol: str,
    period: str = Query(default="1h"),
    kline_count: int = Query(default=100, ge=20, le=500),
):
    """综合分析: 形态 + 共振 + 成交量异动 + 支撑阻力"""
    try:
        from backend.services.data_center import data_center as _dc
        _kl = _dc.get_klines(symbol, period, count=kline_count)
        klines = _kl.rows[-kline_count:] if len(_kl.rows) > kline_count else _kl.rows
        if not klines:
            klines = kline_service.get_klines_from_db(symbol, period, count=kline_count)
        if not klines:
            raise HTTPException(status_code=404, detail=f"No data for {symbol}/{period}")

        current_price = float(klines[-1]["close"])

        # 1. 形态检测
        patterns = detect_patterns(klines)
        latest_pats = sorted(
            [{"name": p.name, "type": p.pattern_type, "confidence": round(p.confidence, 2),
              "timestamp": p.timestamp} for p in patterns],
            key=lambda p: -p["timestamp"]
        )[:5]

        # 2. 多周期共振
        resonance_periods = ["5m", "15m", "1h", "4h", "1d"]
        klines_by_period = {}
        for p in resonance_periods:
            try:
                _r = _dc.get_klines(symbol, p, count=50)
                klines_by_period[p] = _r.rows[-50:] if len(_r.rows) > 50 else _r.rows
            except Exception:
                klines_by_period[p] = kline_service.get_klines_from_db(symbol, p, count=50)

        resonance = compute_resonance(symbol, klines_by_period)

        # 3. 成交量异动
        anomalies = volume_detector.detect(klines, symbol)
        volume_events = sorted(
            [{"timestamp": a.timestamp, "type": a.anomaly_type, "severity": a.severity,
              "description": a.description, "zscore": a.volume_zscore}
             for a in anomalies],
            key=lambda e: -e["timestamp"]
        )[:3]

        # 4. 支撑阻力
        sr_result = calculate_support_resistance(symbol, period, klines)
        nearest_support = sr_result.supports[0].price if sr_result.supports else None
        nearest_resistance = sr_result.resistances[0].price if sr_result.resistances else None

        # 5. 生成综合摘要和信号
        signals = []
        summary_parts = [f"{symbol} {period}综合分析"]  # noqa: F841

        # 共振信号
        if resonance.resonance_level in ("strong_bullish", "bullish"):
            signals.append(f"多周期共振偏多 (得分 {resonance.resonance_score:.0f})")

        elif resonance.resonance_level in ("strong_bearish", "bearish"):
            signals.append(f"多周期共振偏空 (得分 {resonance.resonance_score:.0f})")
        else:
            signals.append("多周期方向分歧，待确认")

        # 形态信号
        bull_pats = [p for p in latest_pats if p["type"] == "bullish"]
        bear_pats = [p for p in latest_pats if p["type"] == "bearish"]
        if bull_pats:
            signals.append(f"看涨形态: {', '.join(p['name'] for p in bull_pats[:2])}")
        if bear_pats:
            signals.append(f"看跌形态: {', '.join(p['name'] for p in bear_pats[:2])}")

        # 成交量异常
        if volume_events and volume_events[0]["severity"] == "high":
            signals.append(f"成交量异常: {volume_events[0]['description']}")

        # S/R
        if nearest_support and current_price > 1e-10:
            dist_pct = ((current_price - nearest_support) / current_price) * 100
            signals.append(f"最近支撑: {nearest_support:.2f} (距离 {dist_pct:.1f}%)")
        if nearest_resistance and current_price > 1e-10:
            dist_pct = ((nearest_resistance - current_price) / current_price) * 100
            signals.append(f"最近阻力: {nearest_resistance:.2f} (距离 {dist_pct:.1f}%)")

        summary = " | ".join(signals) if signals else "数据不足"

        return ComprehensiveAnalysisResponse(
            symbol=symbol,
            period=period,
            current_price=round(current_price, 4),
            pattern_count=len(patterns),
            latest_patterns=latest_pats,
            resonance_score=resonance.resonance_score,
            resonance_level=resonance.resonance_level,
            volume_anomaly_count=len(anomalies),
            latest_volume_events=volume_events,
            nearest_support=round(nearest_support, 4) if nearest_support else None,
            nearest_resistance=round(nearest_resistance, 4) if nearest_resistance else None,
            pivot=round(sr_result.pivot, 4),
            summary=summary,
            signals=signals,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Comprehensive analysis failed for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
