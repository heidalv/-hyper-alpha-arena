"""
K线形态识别 API 路由
"""

from fastapi import APIRouter, Query, HTTPException
from typing import List, Optional
from pydantic import BaseModel, Field
import logging

from backend.services.candlestick_pattern_service import detect_patterns, DetectedPattern
from backend.services.kline_data_service import kline_service
from backend.services.kline_cache_service import kline_cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/klines", tags=["K线形态"])

# ── 响应模型 ──

class PatternItem(BaseModel):
    id: str
    name: str
    pattern_type: str
    timestamp: int
    confidence: float
    description: str
    trading_hints: List[str] = Field(default_factory=list)
    reliability: str = "medium"

    @classmethod
    def from_detected(cls, p: DetectedPattern) -> "PatternItem":
        return cls(
            id=p.id,
            name=p.name,
            pattern_type=p.pattern_type,
            timestamp=p.timestamp,
            confidence=round(p.confidence, 3),
            description=p.description,
            trading_hints=p.trading_hints,
            reliability=p.reliability,
        )


class PatternDetectRequest(BaseModel):
    """通过传入 kline 数据直接检测"""
    symbol: str
    period: str = "1h"
    klines: List[dict]
    min_confidence: float = Field(default=0.3, ge=0.1, le=1.0)


class PatternDetectResponse(BaseModel):
    symbol: str
    period: str
    total: int
    bullish: int
    bearish: int
    neutral: int
    patterns: List[PatternItem]


class PatternListResponse(BaseModel):
    patterns: List[dict]  # kline_patterns.json 中的原始定义


class AvailablePatternsResponse(BaseModel):
    patterns: List[dict]


# ── 路由 ──

@router.post("/patterns/detect", response_model=PatternDetectResponse)
async def detect_patterns_from_data(req: PatternDetectRequest):
    """通过传入的 K 线数据直接检测形态"""
    try:
        patterns = detect_patterns(req.klines, min_confidence=req.min_confidence)

        bullish = sum(1 for p in patterns if p.pattern_type == "bullish")
        bearish = sum(1 for p in patterns if p.pattern_type == "bearish")
        neutral = sum(1 for p in patterns if p.pattern_type == "neutral")

        items = [PatternItem.from_detected(p) for p in patterns]
        kline_cache.set_patterns(req.symbol, req.period, items)

        return PatternDetectResponse(
            symbol=req.symbol,
            period=req.period,
            total=len(items),
            bullish=bullish,
            bearish=bearish,
            neutral=neutral,
            patterns=items,
        )
    except Exception as e:
        logger.error(f"Pattern detection failed: {e}")
        raise HTTPException(status_code=500, detail=f"Pattern detection failed: {str(e)}")


@router.get("/patterns/{symbol}", response_model=PatternDetectResponse)
async def get_patterns_for_symbol(
    symbol: str,
    period: str = Query(default="1h", description="K线周期"),
    count: int = Query(default=100, ge=10, le=500, description="使用的K线数量"),
    min_confidence: float = Query(default=0.3, ge=0.1, le=1.0),
):
    """从数据库获取K线并检测形态, 带缓存"""
    try:
        # 先查缓存
        cached = kline_cache.get_patterns(symbol, period)
        if cached:
            _cached_items = [PatternItem(**p) if isinstance(p, dict) else p for p in cached]
            patterns = [DetectedPattern(
                id=p.id, name=p.name, pattern_type=p.pattern_type,
                timestamp=p.timestamp, confidence=p.confidence,
                description=p.description,
                trading_hints=p.trading_hints, reliability=p.reliability
            ) for p in _cached_items]
        else:
            try:
                from backend.services.data_center import data_center as _dc
                _r = _dc.get_klines(symbol, period, count=count)
                klines = _r.rows[-count:] if len(_r.rows) > count else _r.rows
            except Exception:
                klines = None
            if not klines:
                klines = kline_service.get_klines_from_db(symbol, period, count=count)
            if not klines:
                return PatternDetectResponse(
                    symbol=symbol, period=period,
                    total=0, bullish=0, bearish=0, neutral=0,
                    patterns=[]
                )
            patterns = detect_patterns(klines, min_confidence=min_confidence)
            items = [PatternItem.from_detected(p) for p in patterns]
            kline_cache.set_patterns(symbol, period, [i.model_dump() for i in items])

        bullish = sum(1 for p in patterns if p.pattern_type == "bullish")
        bearish = sum(1 for p in patterns if p.pattern_type == "bearish")
        neutral = sum(1 for p in patterns if p.pattern_type == "neutral")

        items = [PatternItem.from_detected(p) for p in patterns]

        return PatternDetectResponse(
            symbol=symbol,
            period=period,
            total=len(items),
            bullish=bullish,
            bearish=bearish,
            neutral=neutral,
            patterns=items,
        )
    except Exception as e:
        logger.error(f"Failed to get patterns for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/patterns/available", response_model=AvailablePatternsResponse)
async def get_available_patterns():
    """获取支持的 K 线形态列表 (从 kline_patterns.json)"""
    import json
    import os

    patterns_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "kline_patterns.json"
    )
    try:
        with open(patterns_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {"patterns": data}
    except FileNotFoundError:
        alt_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "data", "kline_patterns.json"
        )
        try:
            with open(alt_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {"patterns": data}
        except FileNotFoundError:
            return {"patterns": []}
