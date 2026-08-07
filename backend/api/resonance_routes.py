"""
多周期共振 + 支撑阻力 API 路由
"""

from fastapi import APIRouter, Query, HTTPException
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
import logging

from backend.services.mtf_resonance_service import compute_resonance, ResonanceResult
from backend.services.support_resistance_service import calculate_support_resistance, SRResult
from backend.services.kline_data_service import kline_service
from backend.services.kline_cache_service import kline_cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/klines", tags=["共振 & 支撑阻力"])


# ── 响应模型 ──

class TFItem(BaseModel):
    period: str
    trend: str
    trend_strength: float
    rsi_value: float
    macd_signal: str
    candle_count: int


class ResonanceResponse(BaseModel):
    symbol: str
    resonance_score: float
    resonance_level: str
    alignment: float
    timeframes: List[TFItem]
    summary: str
    signals: List[str]


class SRLevelItem(BaseModel):
    price: float
    label: str
    level_type: str
    method: str
    strength: float


class SRResponse(BaseModel):
    symbol: str
    period: str
    current_price: float
    supports: List[SRLevelItem]
    resistances: List[SRLevelItem]
    pivot: float


# ── 路由 ──

@router.get("/resonance/{symbol}", response_model=ResonanceResponse)
async def get_resonance(symbol: str):
    """
    多周期共振分析。
    自动获取 5m/15m/1h/4h/1d 周期的 K 线数据并计算共振评分。
    """
    try:
        # 查缓存
        cached = kline_cache.get_resonance(symbol)
        if cached:
            return cached

        # 获取各周期 K 线
        periods = ["5m", "15m", "1h", "4h", "1d"]
        klines_by_period: Dict[str, List[Dict]] = {}

        for period in periods:
            klines = kline_service.get_klines_from_db(symbol, period, count=50)
            if klines:
                klines_by_period[period] = klines

        result = compute_resonance(symbol, klines_by_period)

        # 写入缓存
        resp_dict = {
            "symbol": result.symbol,
            "resonance_score": result.resonance_score,
            "resonance_level": result.resonance_level,
            "alignment": result.alignment,
            "timeframes": [
                {
                    "period": t.period,
                    "trend": t.trend,
                    "trend_strength": round(t.trend_strength, 2),
                    "rsi_value": round(t.rsi_value, 1),
                    "macd_signal": t.macd_signal,
                    "candle_count": t.candle_count,
                }
                for t in result.timeframes
            ],
            "summary": result.summary,
            "signals": result.signals,
        }
        kline_cache.set_resonance(symbol, resp_dict)

        return resp_dict

    except Exception as e:
        logger.error(f"Resonance analysis failed for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sr-levels/{symbol}", response_model=SRResponse)
async def get_sr_levels(
    symbol: str,
    period: str = Query(default="1d", description="用于计算摆荡高低点的周期"),
    count: int = Query(default=100, ge=20, le=500, description="K线数量"),
    method: str = Query(default="all", description="计算方法: pivot, fibonacci, swing, volume, round, all"),
):
    """
    自动计算支撑阻力位。
    使用多种方法（枢轴点、斐波那契、摆荡高低点、成交量分布、整数关口）。
    """
    try:
        # 查缓存
        cached = kline_cache.get_sr(symbol, period, method)
        if cached:
            return cached

        klines = kline_service.get_klines_from_db(symbol, period, count=count)
        if not klines:
            raise HTTPException(status_code=404, detail=f"No kline data for {symbol}/{period}")

        result = calculate_support_resistance(symbol, period, klines)

        resp_dict = {
            "symbol": result.symbol,
            "period": result.period,
            "current_price": result.current_price,
            "supports": [
                {"price": s.price, "label": s.label, "level_type": s.level_type,
                 "method": s.method, "strength": round(s.strength, 2)}
                for s in result.supports[:6]
            ],
            "resistances": [
                {"price": r.price, "label": r.label, "level_type": r.level_type,
                 "method": r.method, "strength": round(r.strength, 2)}
                for r in result.resistances[:6]
            ],
            "pivot": result.pivot,
        }

        # 写缓存
        kline_cache.set_sr(symbol, period, method, resp_dict)

        return resp_dict

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"SR calculation failed for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
