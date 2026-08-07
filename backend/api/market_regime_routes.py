"""
Market Regime Classification API routes
"""

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import logging

from backend.database.connection import SessionLocal
from backend.database.models import MarketRegimeConfig
from backend.services.market_regime_service import get_market_regime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/market-regime", tags=["market-regime"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Pydantic models
class RegimeIndicators(BaseModel):
    cvd_ratio: float = 0.0  # CVD / Total Notional
    oi_delta: float = 0.0   # OI change percentage
    taker_ratio: float = 0.0
    price_atr: float = 0.0
    rsi: float = 50.0


class MarketRegimeResponse(BaseModel):
    symbol: str
    regime: str
    direction: str
    confidence: float
    reason: str
    indicators: RegimeIndicators


class BatchRegimeRequest(BaseModel):
    symbols: List[str]
    timeframe: str = "5m"
    config_id: Optional[int] = None
    timestamp_ms: Optional[int] = None  # For backtesting support


class BatchRegimeResponse(BaseModel):
    results: List[MarketRegimeResponse]
    errors: List[Dict[str, str]]


class RegimeConfigResponse(BaseModel):
    id: int
    name: str
    is_default: bool
    rolling_window: int
    breakout_cvd_z: float
    breakout_oi_z: float
    breakout_price_atr: float
    breakout_taker_high: float
    breakout_taker_low: float
    absorption_cvd_z: float
    absorption_price_atr: float
    trap_cvd_z: float
    trap_oi_z: float
    exhaustion_cvd_z: float
    exhaustion_rsi_high: float
    exhaustion_rsi_low: float
    stop_hunt_range_atr: float
    stop_hunt_close_atr: float
    noise_cvd_z: float


class RegimeConfigUpdateRequest(BaseModel):
    """Request model for updating regime config"""
    rolling_window: Optional[int] = None
    breakout_cvd_z: Optional[float] = None
    breakout_oi_z: Optional[float] = None
    breakout_price_atr: Optional[float] = None
    breakout_taker_high: Optional[float] = None
    breakout_taker_low: Optional[float] = None
    absorption_cvd_z: Optional[float] = None
    absorption_price_atr: Optional[float] = None
    trap_cvd_z: Optional[float] = None
    trap_oi_z: Optional[float] = None
    exhaustion_cvd_z: Optional[float] = None
    exhaustion_rsi_high: Optional[float] = None
    exhaustion_rsi_low: Optional[float] = None
    stop_hunt_range_atr: Optional[float] = None
    stop_hunt_close_atr: Optional[float] = None
    noise_cvd_z: Optional[float] = None


@router.get("/{symbol}", response_model=MarketRegimeResponse)
def get_regime_for_symbol(
    symbol: str,
    timeframe: str = "5m",
    config_id: Optional[int] = None,
    timestamp_ms: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """Get market regime classification for a single symbol"""
    try:
        result = get_market_regime(db, symbol, timeframe, config_id, timestamp_ms)
        return MarketRegimeResponse(
            symbol=symbol,
            regime=result["regime"],
            direction=result["direction"],
            confidence=result["confidence"],
            reason=result["reason"],
            indicators=RegimeIndicators(**result["indicators"])
        )
    except Exception as e:
        logger.error(f"Failed to get market regime for {symbol}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get market regime: {str(e)}"
        )


@router.post("/batch", response_model=BatchRegimeResponse)
def get_regime_batch(
    request: BatchRegimeRequest,
    db: Session = Depends(get_db)
):
    """Get market regime classification for multiple symbols"""
    results = []
    errors = []

    for symbol in request.symbols:
        try:
            result = get_market_regime(
                db, symbol, request.timeframe, request.config_id, request.timestamp_ms
            )
            results.append(MarketRegimeResponse(
                symbol=symbol,
                regime=result["regime"],
                direction=result["direction"],
                confidence=result["confidence"],
                reason=result["reason"],
                indicators=RegimeIndicators(**result["indicators"])
            ))
        except Exception as e:
            logger.warning(f"Failed to get regime for {symbol}: {e}")
            errors.append({"symbol": symbol, "error": str(e)})

    return BatchRegimeResponse(results=results, errors=errors)


@router.get("/configs/list", response_model=List[RegimeConfigResponse])
def list_regime_configs(db: Session = Depends(get_db)):
    """List all market regime configurations"""
    try:
        configs = db.query(MarketRegimeConfig).all()
        return [
            RegimeConfigResponse(
                id=c.id,
                name=c.name,
                is_default=c.is_default or False,
                rolling_window=c.rolling_window or 48,
                breakout_cvd_z=c.breakout_cvd_z or 1.5,
                breakout_oi_z=c.breakout_oi_z or 1.0,
                breakout_price_atr=c.breakout_price_atr or 0.5,
                breakout_taker_high=c.breakout_taker_high or 1.8,
                breakout_taker_low=c.breakout_taker_low or 0.55,
                absorption_cvd_z=c.absorption_cvd_z or 1.5,
                absorption_price_atr=c.absorption_price_atr or 0.3,
                trap_cvd_z=c.trap_cvd_z or 1.0,
                trap_oi_z=c.trap_oi_z or -1.0,
                exhaustion_cvd_z=c.exhaustion_cvd_z or 1.0,
                exhaustion_rsi_high=c.exhaustion_rsi_high or 70.0,
                exhaustion_rsi_low=c.exhaustion_rsi_low or 30.0,
                stop_hunt_range_atr=c.stop_hunt_range_atr or 1.0,
                stop_hunt_close_atr=c.stop_hunt_close_atr or 0.3,
                noise_cvd_z=c.noise_cvd_z or 0.5
            )
            for c in configs
        ]
    except Exception as e:
        logger.error(f"Failed to list regime configs: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list configs: {str(e)}"
        )


@router.put("/configs/{config_id}", response_model=RegimeConfigResponse)
def update_regime_config(
    config_id: int,
    request: RegimeConfigUpdateRequest,
    db: Session = Depends(get_db)
):
    """Update a market regime configuration"""
    try:
        config = db.query(MarketRegimeConfig).filter(MarketRegimeConfig.id == config_id).first()
        if not config:
            raise HTTPException(status_code=404, detail=f"Config {config_id} not found")

        # Update only provided fields
        update_data = request.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            if value is not None:
                setattr(config, field, value)

        db.commit()
        db.refresh(config)

        return RegimeConfigResponse(
            id=config.id,
            name=config.name,
            is_default=config.is_default or False,
            rolling_window=config.rolling_window or 48,
            breakout_cvd_z=config.breakout_cvd_z or 1.5,
            breakout_oi_z=config.breakout_oi_z or 1.0,
            breakout_price_atr=config.breakout_price_atr or 0.5,
            breakout_taker_high=config.breakout_taker_high or 1.8,
            breakout_taker_low=config.breakout_taker_low or 0.55,
            absorption_cvd_z=config.absorption_cvd_z or 1.5,
            absorption_price_atr=config.absorption_price_atr or 0.3,
            trap_cvd_z=config.trap_cvd_z or 1.0,
            trap_oi_z=config.trap_oi_z or -1.0,
            exhaustion_cvd_z=config.exhaustion_cvd_z or 1.0,
            exhaustion_rsi_high=config.exhaustion_rsi_high or 70.0,
            exhaustion_rsi_low=config.exhaustion_rsi_low or 30.0,
            stop_hunt_range_atr=config.stop_hunt_range_atr or 1.0,
            stop_hunt_close_atr=config.stop_hunt_close_atr or 0.3,
            noise_cvd_z=config.noise_cvd_z or 0.5
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update regime config {config_id}: {e}")
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update config: {str(e)}"
        )
