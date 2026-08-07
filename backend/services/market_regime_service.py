"""
Market Regime Classification Service

Classifies market conditions into 7 regime types:
1. Stop Hunt - Price spike through key level then reversal
2. Absorption - Strong flow but price doesn't move
3. Breakout - Trend initiation with aligned signals
4. Continuation - Trend continuation
5. Exhaustion - Trend exhaustion at extremes
6. Trap - Bull/bear trap (strong flow but OI decreasing)
7. Noise - No clear signal

Indicator definitions (per planning document):
- cvd_ratio: CVD / Total Notional (not z-score)
- taker_ratio: ln(buy_notional / sell_notional) - log transformation for symmetry
- oi_delta: OI change percentage
- price_atr: Price Change / ATR
- rsi: RSI14
"""

import math
import logging
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from backend.database.models import MarketRegimeConfig, CryptoKline
from backend.services.technical_indicators import calculate_indicators
from backend.services.market_flow_indicators import get_flow_indicators_for_prompt, TIMEFRAME_MS

logger = logging.getLogger(__name__)


# Regime type constants
REGIME_STOP_HUNT = "stop_hunt"
REGIME_ABSORPTION = "absorption"
REGIME_BREAKOUT = "breakout"
REGIME_CONTINUATION = "continuation"
REGIME_EXHAUSTION = "exhaustion"
REGIME_TRAP = "trap"
REGIME_NOISE = "noise"

# Direction constants
DIRECTION_BULLISH = "bullish"
DIRECTION_BEARISH = "bearish"
DIRECTION_NEUTRAL = "neutral"


def get_default_config(db: Session) -> Optional[MarketRegimeConfig]:
    """Get default regime config from database"""
    return db.query(MarketRegimeConfig).filter(
        MarketRegimeConfig.is_default == True
    ).first()


def calculate_direction(cvd_ratio: float, taker_log_ratio: float, price_atr: float) -> str:
    """
    Calculate direction by voting: cvd + taker + price.
    Note: taker_log_ratio is already log-transformed, so >0 means bullish, <0 means bearish.
    """
    votes = 0
    if cvd_ratio > 0:
        votes += 1
    elif cvd_ratio < 0:
        votes -= 1
    if taker_log_ratio > 0:  # log(buy/sell) > 0 means buy > sell
        votes += 1
    elif taker_log_ratio < 0:
        votes -= 1
    if price_atr > 0:
        votes += 1
    elif price_atr < 0:
        votes -= 1

    if votes >= 2:
        return DIRECTION_BULLISH
    elif votes <= -2:
        return DIRECTION_BEARISH
    return DIRECTION_NEUTRAL


def calculate_confidence(
    cvd_ratio: float, taker_log_ratio: float, oi_delta: float, price_atr: float
) -> float:
    """Calculate confidence score (0-1) based on signal strength"""
    # Normalize each indicator to 0-1 range
    # cvd_ratio: typical range -0.5 to 0.5, cap at 0.3
    # taker_log_ratio: typical range -1 to 1 (log scale)
    # oi_delta: typical range -5% to 5%
    # price_atr: typical range -2 to 2
    score = (
        0.3 * min(abs(cvd_ratio), 0.3) / 0.3 +
        0.2 * min(abs(taker_log_ratio), 1.0) / 1.0 +
        0.2 * min(abs(oi_delta), 5.0) / 5.0 +
        0.3 * min(abs(price_atr), 2.0) / 2.0
    )
    return max(0.0, min(1.0, score))


def classify_regime(
    cvd_ratio: float,
    taker_log_ratio: float,
    oi_delta: float,
    price_atr: float,
    rsi: float,
    price_range_atr: float,
    config: MarketRegimeConfig
) -> Tuple[str, str]:
    """
    Classify market regime based on indicators.
    Returns (regime_type, reason)

    Priority order:
    1. Stop Hunt - spike and reversal
    2. Breakout - strong CVD + price move + (Taker extreme OR OI increase)
    3. Exhaustion - strong CVD + OI decrease + RSI extreme
    4. Trap - strong CVD + OI decrease significantly
    5. Absorption - strong CVD but price doesn't move
    6. Continuation - CVD aligned with price movement
    7. Noise - no clear pattern

    Note: Taker thresholds should be set to capture ~25% as extreme.
    Default: taker_high=33, taker_low=0.03 (log threshold ±3.5)
    """
    # Thresholds from config
    cvd_strong = config.breakout_cvd_z * 0.1  # ~0.15 for strong flow
    cvd_weak = cvd_strong / 3  # ~0.05 for weak flow
    price_breakout = config.breakout_price_atr + 0.2  # ~0.5 for breakout
    price_move = config.absorption_price_atr  # ~0.3 for movement
    oi_increase = config.breakout_oi_z  # OI increase threshold
    oi_decrease = config.trap_oi_z  # OI decrease threshold

    # Taker extreme check (using log thresholds)
    taker_high_log = math.log(config.breakout_taker_high) if config.breakout_taker_high > 0 else 3.5
    taker_low_log = math.log(config.breakout_taker_low) if config.breakout_taker_low > 0 else -3.5
    is_taker_extreme = taker_log_ratio > taker_high_log or taker_log_ratio < taker_low_log

    # Direction alignment check
    cvd_price_aligned = (cvd_ratio > 0 and price_atr > 0) or (cvd_ratio < 0 and price_atr < 0)

    # 1. Stop Hunt: large range but close near open (spike and reversal)
    if (price_range_atr > config.stop_hunt_range_atr and
        abs(price_atr) < config.stop_hunt_close_atr):
        return REGIME_STOP_HUNT, "Price spiked but closed near open"

    # 2. Breakout: strong CVD + price move + (Taker extreme OR OI increase)
    # Additional check: body must be significant portion of range (not spike-and-reverse)
    is_cvd_strong = abs(cvd_ratio) > cvd_strong
    is_price_breakout = abs(price_atr) > price_breakout
    is_oi_increase = oi_delta > oi_increase
    # Body ratio: if price spiked but reversed (long shadow), it's not a true breakout
    body_ratio = abs(price_atr) / price_range_atr if price_range_atr > 0 else 1.0
    is_solid_move = body_ratio > 0.4  # Body must be >40% of range

    if is_cvd_strong and is_price_breakout and cvd_price_aligned and is_solid_move and (is_taker_extreme or is_oi_increase):
        direction = "Bullish" if cvd_ratio > 0 else "Bearish"
        return REGIME_BREAKOUT, f"{direction} breakout with aligned signals"

    # 3. Exhaustion: strong CVD + OI decrease + RSI extreme
    is_oi_decrease = oi_delta < oi_decrease
    rsi_extreme = rsi > config.exhaustion_rsi_high or rsi < config.exhaustion_rsi_low

    if is_cvd_strong and is_oi_decrease and rsi_extreme:
        return REGIME_EXHAUSTION, "Trend exhaustion at RSI extreme"

    # 4. Trap: strong CVD + OI decrease significantly
    if is_cvd_strong and is_oi_decrease:
        return REGIME_TRAP, "Strong flow but positions closing (trap)"

    # 5. Absorption: strong CVD but price doesn't move
    is_price_move = abs(price_atr) > price_move
    if is_cvd_strong and not is_price_move:
        return REGIME_ABSORPTION, "Strong flow absorbed without price movement"

    # 6. Continuation: CVD aligned with price movement
    is_cvd_weak = abs(cvd_ratio) > cvd_weak
    if is_cvd_weak and is_price_move and cvd_price_aligned:
        direction = "Bullish" if cvd_ratio > 0 else "Bearish"
        return REGIME_CONTINUATION, f"{direction} trend continuation"

    # 7. Noise: no clear pattern
    return REGIME_NOISE, "No clear market regime detected"


def fetch_kline_data(
    db: Session, symbol: str, period: str = "5m", limit: int = 50,
    current_time_ms: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Fetch K-line data for technical indicator calculation.
    Returns list of dicts with timestamp, open, high, low, close, volume.

    Args:
        db: Database session (kept for API compatibility; MarketSessionLocal used internally for PG)
        symbol: Trading symbol
        period: Timeframe (1m, 5m, 15m, etc.)
        limit: Number of candles to fetch
        current_time_ms: Optional timestamp for historical queries (backtesting)
    """
    # M1 收口：统一 K 线查询门面（数据中心）
    from backend.services.kline_data_service import kline_service
    end_ts = int(current_time_ms) // 1000 if current_time_ms else None
    klines = kline_service.query_klines(
        symbol, period, limit=limit, end_ts=end_ts, order="desc",
    )
    if not klines:
        return []

    # 原始语义：desc 拉取后反转为时间正序
    result = []
    for k in reversed(klines):
        result.append({
            "timestamp": k["timestamp"],
            "open": float(k.get("open") or 0),
            "high": float(k.get("high") or 0),
            "low": float(k.get("low") or 0),
            "close": float(k.get("close") or 0),
            "volume": float(k.get("volume") or 0)
        })
    return result


def calculate_price_metrics(kline_data: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    Calculate price-based metrics using technical indicators.
    Returns: price_atr, price_range_atr, rsi
    """
    import math
    _nan = float('nan')
    if len(kline_data) < 15:  # Need at least 15 bars for ATR14 and RSI14
        # [2026-07-10] 数据不足返回 NaN 而非 {0,0,50} 占位。
        # 原 rsi=50/atr=0 会被 classify_regime 当成"真实横盘+零波动"误判。
        return {"price_atr": _nan, "price_range_atr": _nan, "rsi": _nan}

    # Calculate ATR and RSI using technical_indicators service
    indicators = calculate_indicators(kline_data, ["ATR14", "RSI14"])

    atr_values = indicators.get("ATR14", [])
    rsi_values = indicators.get("RSI14", [])

    # Get latest values（数据不足时 calculate_indicators 返回 NaN，此处自然传播）
    atr = atr_values[-1] if atr_values else _nan
    rsi = rsi_values[-1] if rsi_values else _nan

    # Calculate price_atr: (close - open) / ATR (normalized price change)
    # [2026-07-10] atr 为 NaN 或 <=0 时不计算（除以 0/NaN 会得 nan/inf）
    if atr == atr and atr > 0 and len(kline_data) >= 1:  # atr==atr 排除 NaN
        latest = kline_data[-1]
        price_change = latest["close"] - latest["open"]
        price_atr = price_change / atr
        # Calculate price_range_atr: (high - low) / ATR
        price_range = latest["high"] - latest["low"]
        price_range_atr = price_range / atr
    else:
        price_atr = _nan
        price_range_atr = _nan

    return {
        "price_atr": price_atr,
        "price_range_atr": price_range_atr,
        "rsi": rsi
    }


def get_market_regime(
    db: Session,
    symbol: str,
    timeframe: str = "5m",
    config_id: Optional[int] = None,
    timestamp_ms: Optional[int] = None
) -> Dict[str, Any]:
    """
    Main entry point: Get market regime classification for a symbol.

    IMPORTANT: This function reuses market_flow_indicators service for CVD, Taker, OI
    to ensure consistency with signal detection system.

    Args:
        db: Database session
        symbol: Trading pair symbol (e.g., "BTC")
        timeframe: Time frame (1m, 5m, 15m, 1h, etc.)
        config_id: Optional config ID, uses default if not specified
        timestamp_ms: Optional timestamp for historical queries (backtesting)

    Returns:
        Dict with regime, direction, confidence, reason, indicators, and debug info
    """
    # Get config
    if config_id:
        config = db.query(MarketRegimeConfig).filter(
            MarketRegimeConfig.id == config_id
        ).first()
    else:
        config = get_default_config(db)

    if not config:
        return {
            "regime": REGIME_NOISE,
            "direction": DIRECTION_NEUTRAL,
            "confidence": 0.0,
            "reason": "No regime config found",
            "indicators": {},
            "debug": {}
        }

    # Validate timeframe
    if timeframe not in TIMEFRAME_MS:
        return {
            "regime": REGIME_NOISE,
            "direction": DIRECTION_NEUTRAL,
            "confidence": 0.0,
            "reason": f"Unsupported timeframe: {timeframe}",
            "indicators": {},
            "debug": {}
        }

    # Get current time if not specified
    if timestamp_ms is None:
        # 修时区 bug：用 UTC-aware 计算 Unix ms，避免 naive utcnow().timestamp() 被当作本地时区
        timestamp_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    # Fetch flow indicators using market_flow_indicators service (REUSE!)
    # Use MarketSessionLocal for PG multi-DB compatibility
    from backend.database.connection import MarketSessionLocal as _MSL
    _market_db = _MSL()
    try:
        flow_data = get_flow_indicators_for_prompt(
            _market_db, symbol, timeframe, ["CVD", "TAKER", "OI_DELTA"], timestamp_ms
        )
    finally:
        _market_db.close()

    cvd_data = flow_data.get("CVD")
    taker_data = flow_data.get("TAKER")
    oi_delta_data = flow_data.get("OI_DELTA")

    # Check if we have enough data
    if not cvd_data or not taker_data:
        return {
            "regime": REGIME_NOISE,
            "direction": DIRECTION_NEUTRAL,
            "confidence": 0.0,
            "reason": "Insufficient market flow data",
            "indicators": {},
            "debug": {"cvd_data": cvd_data, "taker_data": taker_data}
        }

    # Extract indicator values
    # CVD ratio: current CVD / total notional (buy + sell)
    cvd_current = cvd_data.get("current", 0)
    taker_buy = taker_data.get("buy", 0)
    taker_sell = taker_data.get("sell", 0)
    total_notional = taker_buy + taker_sell

    cvd_ratio = cvd_current / total_notional if total_notional > 0 else 0.0

    # Taker log ratio: ln(buy/sell) for symmetry around 0
    if taker_buy > 0 and taker_sell > 0:
        taker_log_ratio = math.log(taker_buy / taker_sell)
    else:
        taker_log_ratio = 0.0

    # OI delta: percentage change
    oi_delta = oi_delta_data.get("current", 0) if oi_delta_data else 0.0

    # Fetch K-line data and calculate price metrics (ATR, RSI)
    kline_data = fetch_kline_data(db, symbol, timeframe, limit=50, current_time_ms=timestamp_ms)
    price_metrics = calculate_price_metrics(kline_data)
    price_atr = price_metrics["price_atr"]
    price_range_atr = price_metrics["price_range_atr"]
    rsi = price_metrics["rsi"]

    # Classify regime
    regime, reason = classify_regime(
        cvd_ratio, taker_log_ratio, oi_delta, price_atr, rsi, price_range_atr, config
    )

    # Calculate direction and confidence
    direction = calculate_direction(cvd_ratio, taker_log_ratio, price_atr)
    confidence = calculate_confidence(cvd_ratio, taker_log_ratio, oi_delta, price_atr)

    return {
        "regime": regime,
        "direction": direction,
        "confidence": round(confidence, 3),
        "reason": reason,
        "indicators": {
            "cvd_ratio": round(cvd_ratio, 4),  # CVD / Total Notional
            "oi_delta": round(oi_delta, 3),    # OI change percentage
            "taker_ratio": round(math.exp(taker_log_ratio), 3),  # buy/sell ratio
            "price_atr": round(price_atr, 3),
            "rsi": round(rsi, 1)
        },
        "debug": {
            "cvd_ratio": round(cvd_ratio, 4),
            "taker_log_ratio": round(taker_log_ratio, 4),
            "oi_delta_pct": round(oi_delta, 3),
            "taker_buy": round(taker_buy, 2),
            "taker_sell": round(taker_sell, 2),
            "total_notional": round(total_notional, 2),
            "timestamp_ms": timestamp_ms,
            "timeframe": timeframe
        }
    }


# Alias for compatibility
def classify_market_regime(
    db: Session,
    symbol: str,
    period: str = "5m",
    timestamp_ms: Optional[int] = None
) -> Dict[str, Any]:
    """Alias for get_market_regime for backward compatibility"""
    return get_market_regime(db, symbol, period, timestamp_ms=timestamp_ms)


# ============================================================================
# Adaptive Trading Parameters (MDSPG Extension)
# ============================================================================

from dataclasses import dataclass, asdict


@dataclass
class AdaptiveParameters:
    """Adaptive trading parameters based on market regime"""
    regime_type: str
    regime_direction: str
    regime_confidence: float
    
    # Position sizing
    position_size_modifier: float  # Multiplier for base position (0.5-1.5)
    
    # Risk management
    stop_loss_atr_multiple: float  # Stop loss as ATR multiple (1.0-3.0)
    take_profit_ratio: float  # TP/SL ratio (1.5-3.0)
    
    # Entry requirements
    entry_confirmation_count: int  # Number of confirming indicators (1-3)
    
    # Strategy recommendation
    recommended_strategy: str  # trend_following/mean_reversion/wait/scalping
    
    # Additional context
    suggested_direction: str  # long/short/neutral
    risk_level: str  # low/medium/high
    notes: str  # Human-readable explanation
    
    # Optional fields with defaults (must be at the end)
    max_position_percent: float = 0.1  # Max position as % of portfolio (default 10%)
    trailing_stop_enabled: bool = False  # Whether to use trailing stop


# Regime-specific parameter configurations
REGIME_ADAPTIVE_PARAMS = {
    REGIME_BREAKOUT: {
        "position_size_modifier": 1.2,
        "stop_loss_atr_multiple": 1.5,
        "take_profit_ratio": 2.5,
        "entry_confirmation_count": 2,
        "recommended_strategy": "trend_following",
        "risk_level": "medium",
        "notes": "Strong breakout detected. Follow the trend with momentum."
    },
    REGIME_CONTINUATION: {
        "position_size_modifier": 1.0,
        "stop_loss_atr_multiple": 1.5,
        "take_profit_ratio": 2.0,
        "entry_confirmation_count": 2,
        "recommended_strategy": "trend_following",
        "risk_level": "medium",
        "notes": "Trend continuation. Enter on pullbacks to moving averages."
    },
    REGIME_ABSORPTION: {
        "position_size_modifier": 0.8,
        "stop_loss_atr_multiple": 1.0,
        "take_profit_ratio": 1.5,
        "entry_confirmation_count": 3,
        "recommended_strategy": "mean_reversion",
        "risk_level": "medium",
        "notes": "Range-bound market. Trade reversals at support/resistance."
    },
    REGIME_EXHAUSTION: {
        "position_size_modifier": 1.0,
        "stop_loss_atr_multiple": 1.5,
        "take_profit_ratio": 2.0,
        "entry_confirmation_count": 2,
        "recommended_strategy": "counter_trend",
        "risk_level": "high",
        "notes": "Trend exhaustion. Consider counter-trend entries with tight stops."
    },
    REGIME_TRAP: {
        "position_size_modifier": 0.7,
        "stop_loss_atr_multiple": 1.0,
        "take_profit_ratio": 1.5,
        "entry_confirmation_count": 3,
        "recommended_strategy": "counter_trend",
        "risk_level": "high",
        "notes": "Bull/bear trap detected. Wait for confirmation before counter-trading."
    },
    REGIME_STOP_HUNT: {
        "position_size_modifier": 0.8,
        "stop_loss_atr_multiple": 2.0,
        "take_profit_ratio": 2.0,
        "entry_confirmation_count": 2,
        "recommended_strategy": "fade_spike",
        "risk_level": "high",
        "notes": "Stop hunt/liquidation cascade. Consider fading the spike after stabilization."
    },
    REGIME_NOISE: {
        "position_size_modifier": 0.5,
        "stop_loss_atr_multiple": 2.0,
        "take_profit_ratio": 1.2,
        "entry_confirmation_count": 3,
        "recommended_strategy": "wait_or_scalp",
        "risk_level": "low",
        "notes": "No clear regime. Reduce position sizes or wait for clearer signals."
    }
}


def get_adaptive_trading_parameters(
    db: Session,
    symbol: str,
    period: str = "1h"
) -> AdaptiveParameters:
    """
    Get adaptive trading parameters based on current market regime.
    
    These parameters help adjust trading strategy based on market conditions:
    - Position sizing (larger in trending markets, smaller in noisy markets)
    - Stop loss distance (tighter in range-bound, wider in volatile)
    - Take profit targets
    - Entry confirmation requirements
    - Strategy recommendations
    
    Args:
        db: Database session
        symbol: Trading symbol (e.g., "BTC")
        period: Timeframe for regime analysis (default "1h")
        
    Returns:
        AdaptiveParameters with recommended trading settings
    """
    # Get current market regime
    regime_data = get_market_regime(db, symbol, period)
    
    regime_type = regime_data.get("regime", REGIME_NOISE)
    direction = regime_data.get("direction", DIRECTION_NEUTRAL)
    confidence = regime_data.get("confidence", 0.5)
    
    # Get base parameters for this regime
    params = REGIME_ADAPTIVE_PARAMS.get(regime_type, REGIME_ADAPTIVE_PARAMS[REGIME_NOISE]).copy()
    
    # Adjust parameters based on confidence
    if confidence < 0.4:
        # Low confidence: reduce position size, increase confirmation requirements
        params["position_size_modifier"] *= 0.7
        params["entry_confirmation_count"] = min(params["entry_confirmation_count"] + 1, 3)
        params["notes"] += " (Low confidence - be cautious)"
    elif confidence > 0.7:
        # High confidence: can be slightly more aggressive
        params["position_size_modifier"] *= 1.1
        params["notes"] += " (High confidence signal)"
    
    # Determine suggested direction
    if regime_type in [REGIME_BREAKOUT, REGIME_CONTINUATION]:
        suggested_direction = "long" if direction == DIRECTION_BULLISH else "short" if direction == DIRECTION_BEARISH else "neutral"
    elif regime_type in [REGIME_EXHAUSTION, REGIME_TRAP]:
        # Counter-trend: opposite direction
        suggested_direction = "short" if direction == DIRECTION_BULLISH else "long" if direction == DIRECTION_BEARISH else "neutral"
    else:
        suggested_direction = "neutral"
    
    return AdaptiveParameters(
        regime_type=regime_type,
        regime_direction=direction,
        regime_confidence=confidence,
        position_size_modifier=round(params["position_size_modifier"], 2),
        max_position_percent=0.1 if params["risk_level"] == "low" else (0.15 if params["risk_level"] == "medium" else 0.2),
        stop_loss_atr_multiple=params["stop_loss_atr_multiple"],
        take_profit_ratio=params["take_profit_ratio"],
        trailing_stop_enabled=regime_type in [REGIME_BREAKOUT, REGIME_CONTINUATION],
        entry_confirmation_count=params["entry_confirmation_count"],
        recommended_strategy=params["recommended_strategy"],
        suggested_direction=suggested_direction,
        risk_level=params["risk_level"],
        notes=params["notes"]
    )


def get_multi_timeframe_regime_consensus(
    db: Session,
    symbol: str,
    timeframes: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Analyze market regime across multiple timeframes to find consensus.
    
    This helps identify stronger signals when multiple timeframes agree,
    and warns when timeframes are conflicting.
    
    Args:
        db: Database session
        symbol: Trading symbol
        timeframes: List of timeframes to analyze (default: 5m, 15m, 1h, 4h)
        
    Returns:
        Dict with:
        - individual_regimes: Dict of regime per timeframe
        - consensus_regime: The dominant regime if any
        - consensus_direction: Dominant direction
        - alignment_score: 0-1 score of how aligned timeframes are
        - recommendation: Trading recommendation
    """
    if timeframes is None:
        timeframes = ["5m", "15m", "1h", "4h"]
    
    individual_regimes = {}
    directions = []
    regimes = []
    confidences = []
    
    for tf in timeframes:
        try:
            regime_data = get_market_regime(db, symbol, tf)
            individual_regimes[tf] = {
                "regime": regime_data.get("regime", REGIME_NOISE),
                "direction": regime_data.get("direction", DIRECTION_NEUTRAL),
                "confidence": regime_data.get("confidence", 0.5)
            }
            regimes.append(regime_data.get("regime", REGIME_NOISE))
            directions.append(regime_data.get("direction", DIRECTION_NEUTRAL))
            confidences.append(regime_data.get("confidence", 0.5))
        except Exception as e:
            logger.warning(f"Failed to get regime for {symbol} {tf}: {e}")
            individual_regimes[tf] = {
                "regime": REGIME_NOISE,
                "direction": DIRECTION_NEUTRAL,
                "confidence": 0.0
            }
    
    # Calculate consensus
    bullish_count = directions.count(DIRECTION_BULLISH)
    bearish_count = directions.count(DIRECTION_BEARISH)
    
    # Direction consensus
    if bullish_count >= len(timeframes) * 0.6:
        consensus_direction = DIRECTION_BULLISH
    elif bearish_count >= len(timeframes) * 0.6:
        consensus_direction = DIRECTION_BEARISH
    else:
        consensus_direction = DIRECTION_NEUTRAL
    
    # Regime consensus - find most common non-noise regime
    regime_counts = {}
    for r in regimes:
        if r != REGIME_NOISE:
            regime_counts[r] = regime_counts.get(r, 0) + 1
    
    if regime_counts:
        consensus_regime = max(regime_counts.keys(), key=lambda x: regime_counts[x])
        if regime_counts[consensus_regime] < len(timeframes) * 0.5:
            consensus_regime = "mixed"
    else:
        consensus_regime = REGIME_NOISE
    
    # Alignment score (how many timeframes agree)
    direction_agreement = max(bullish_count, bearish_count, len(timeframes) - bullish_count - bearish_count)
    alignment_score = direction_agreement / len(timeframes)
    
    # Average confidence
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.5
    
    # Generate recommendation
    if alignment_score >= 0.75 and consensus_regime not in [REGIME_NOISE, "mixed"]:
        if consensus_direction == DIRECTION_BULLISH:
            recommendation = "Strong bullish alignment. Look for long entries."
        elif consensus_direction == DIRECTION_BEARISH:
            recommendation = "Strong bearish alignment. Look for short entries."
        else:
            recommendation = "Aligned but neutral. Wait for directional clarity."
    elif alignment_score >= 0.5:
        recommendation = "Moderate alignment. Use smaller position sizes."
    else:
        recommendation = "Timeframes conflicting. Wait or use very small positions."
    
    return {
        "symbol": symbol,
        "timeframes_analyzed": timeframes,
        "individual_regimes": individual_regimes,
        "consensus_regime": consensus_regime,
        "consensus_direction": consensus_direction,
        "alignment_score": round(alignment_score, 2),
        "average_confidence": round(avg_confidence, 2),
        "recommendation": recommendation,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


def get_regime_description(regime_type: str, direction: str) -> str:
    """
    Get human-readable description of a market regime.
    
    Useful for prompt generation and UI display.
    """
    descriptions = {
        REGIME_BREAKOUT: {
            DIRECTION_BULLISH: "看涨突破 (Bullish Breakout) - 价格强势上涨，伴随成交量和持仓增加",
            DIRECTION_BEARISH: "看跌突破 (Bearish Breakout) - 价格强势下跌，伴随成交量和持仓增加",
            DIRECTION_NEUTRAL: "突破状态 (Breakout) - 价格突破但方向不明确"
        },
        REGIME_CONTINUATION: {
            DIRECTION_BULLISH: "多头延续 (Bullish Continuation) - 上涨趋势持续中",
            DIRECTION_BEARISH: "空头延续 (Bearish Continuation) - 下跌趋势持续中",
            DIRECTION_NEUTRAL: "趋势延续 (Continuation) - 当前趋势持续"
        },
        REGIME_ABSORPTION: {
            DIRECTION_BULLISH: "多头吸收 (Bullish Absorption) - 买盘强但价格未涨，可能蓄势",
            DIRECTION_BEARISH: "空头吸收 (Bearish Absorption) - 卖盘强但价格未跌，可能蓄势",
            DIRECTION_NEUTRAL: "横盘吸收 (Absorption) - 大量成交但价格稳定，震荡市"
        },
        REGIME_EXHAUSTION: {
            DIRECTION_BULLISH: "多头衰竭 (Bullish Exhaustion) - 上涨动能减弱，可能反转",
            DIRECTION_BEARISH: "空头衰竭 (Bearish Exhaustion) - 下跌动能减弱，可能反转",
            DIRECTION_NEUTRAL: "趋势衰竭 (Exhaustion) - 当前趋势可能即将反转"
        },
        REGIME_TRAP: {
            DIRECTION_BULLISH: "多头陷阱 (Bull Trap) - 虚假上涨，谨慎追多",
            DIRECTION_BEARISH: "空头陷阱 (Bear Trap) - 虚假下跌，谨慎追空",
            DIRECTION_NEUTRAL: "诱多/诱空 (Trap) - 虚假突破，等待确认"
        },
        REGIME_STOP_HUNT: {
            DIRECTION_BULLISH: "多头狩猎 (Long Stop Hunt) - 清洗空头后可能反弹",
            DIRECTION_BEARISH: "空头狩猎 (Short Stop Hunt) - 清洗多头后可能回落",
            DIRECTION_NEUTRAL: "止损狩猎 (Stop Hunt) - 价格剧烈波动后回归"
        },
        REGIME_NOISE: {
            DIRECTION_BULLISH: "噪音 - 略偏多",
            DIRECTION_BEARISH: "噪音 - 略偏空",
            DIRECTION_NEUTRAL: "噪音 (Noise) - 无明确市场状态，建议观望"
        }
    }
    
    regime_desc = descriptions.get(regime_type, descriptions[REGIME_NOISE])
    return regime_desc.get(direction, regime_desc[DIRECTION_NEUTRAL])
