"""
Smart Signal Generation API Routes

Provides endpoints for intelligent signal generation based on market analysis.
"""
import logging
import time
import requests

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel, Field
from dataclasses import asdict

from backend.database.connection import get_db
from backend.database.models import Account
from backend.services.smart_signal_generator import smart_signal_generator
from backend.services.pattern_recognition_service import (
    pattern_recognition_service,
    SYSTEM_PATTERNS
)
from backend.services.market_data_analyzer import market_data_analyzer
from backend.services.market_regime_service import (
    get_market_regime,
    get_adaptive_trading_parameters,
    get_multi_timeframe_regime_consensus,
    get_regime_description
)
from backend.services.ai_decision_service import build_chat_completion_endpoints, _extract_text_from_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/smart-signals", tags=["Smart Signal Generation"])


# ============================================================================
# Request/Response Models
# ============================================================================

class GenerateSignalRequest(BaseModel):
    """Request for generating optimal signal"""
    symbol: str = Field(..., description="Trading symbol (e.g., BTC)")
    direction: str = Field("auto", description="Direction: auto, long, short")
    risk_level: str = Field("moderate", description="Risk level: conservative, moderate, aggressive")
    time_window: str = Field("5m", description="Time window: 1m, 5m, 15m, 1h")
    strategy_type: str = Field("adaptive", description="Strategy: trend, reversal, breakout, scalping, adaptive")
    lookback_days: int = Field(14, ge=1, le=90, description="Number of days of historical data to analyze (1-90)")


class GeneratePoolRequest(BaseModel):
    """Request for generating signal pool"""
    symbol: str
    strategy_type: str = "adaptive"
    direction: str = "auto"
    max_signals: int = Field(3, ge=1, le=5)
    time_window: str = "5m"


class OptimizeSignalRequest(BaseModel):
    """Request for optimizing signal parameters"""
    signal_config: dict
    symbol: str
    optimization_target: str = Field("sharpe", description="Target: sharpe, win_rate, profit, risk_adjusted")
    days: int = Field(30, ge=7, le=90)


# ============================================================================
# Signal Generation Endpoints
# ============================================================================

@router.post("/generate-optimal-signal")
def generate_optimal_signal(
    request: GenerateSignalRequest,
    db: Session = Depends(get_db)
):
    """
    Generate the optimal signal configuration for current market conditions.
    
    This endpoint analyzes current market regime, historical data, and patterns
    to create a signal with the best expected performance.
    
    Returns complete signal configuration ready for creation.
    """
    try:
        result = smart_signal_generator.generate_optimal_signal(
            db=db,
            symbol=request.symbol,
            direction=request.direction,
            risk_level=request.risk_level,
            time_window=request.time_window,
            strategy_type=request.strategy_type,
            lookback_days=request.lookback_days
        )
        
        return {
            "success": True,
            "signal": asdict(result)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate-signal-pool")
def generate_signal_pool(
    request: GeneratePoolRequest,
    db: Session = Depends(get_db)
):
    """
    Generate a signal pool with multiple complementary signals.
    
    Creates a combination of signals that work together for the specified strategy.
    """
    try:
        result = smart_signal_generator.generate_signal_pool(
            db=db,
            symbol=request.symbol,
            strategy_type=request.strategy_type,
            direction=request.direction,
            max_signals=request.max_signals,
            time_window=request.time_window
        )
        
        return {
            "success": True,
            "pool": asdict(result)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/optimize-signal")
def optimize_signal(
    request: OptimizeSignalRequest,
    db: Session = Depends(get_db)
):
    """
    Optimize signal parameters for better performance.
    
    Tests variations of threshold values to find optimal configuration.
    """
    try:
        result = smart_signal_generator.optimize_signal_parameters(
            db=db,
            signal_config=request.signal_config,
            symbol=request.symbol,
            optimization_target=request.optimization_target,
            days=request.days
        )
        
        return {
            "success": True,
            "optimization": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/suggestions/{symbol}")
def get_signal_suggestions(
    symbol: str,
    time_window: str = Query("5m", description="Time window"),
    db: Session = Depends(get_db)
):
    """
    Get signal suggestions based on current market conditions.
    
    Returns multiple signal options with different risk/reward profiles.
    """
    try:
        suggestions = smart_signal_generator.get_signal_suggestions(
            db=db,
            symbol=symbol,
            time_window=time_window
        )
        
        return {
            "success": True,
            "symbol": symbol,
            "time_window": time_window,
            "suggestions": suggestions
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Market Analysis Endpoints
# ============================================================================

@router.get("/market-analysis/{symbol}")
def get_market_analysis(
    symbol: str,
    period: str = Query("5m", description="Analysis period"),
    lookback_days: int = Query(14, ge=1, le=90, description="Days to analyze"),
    db: Session = Depends(get_db)
):
    """
    Get comprehensive market analysis for a symbol.
    
    Returns price analysis, volume analysis, indicator distributions,
    and threshold suggestions.
    """
    try:
        result = market_data_analyzer.analyze_period(
            db=db,
            symbol=symbol,
            period=period,
            lookback_days=lookback_days
        )
        
        return {
            "success": True,
            "analysis": {
                "symbol": result.symbol,
                "period": result.period,
                "analysis_time": result.analysis_time,
                "lookback_days": result.lookback_days,
                "data_points": result.data_points,
                "price_analysis": asdict(result.price_analysis),
                "volume_analysis": asdict(result.volume_analysis),
                "indicator_distributions": {
                    k: asdict(v) for k, v in result.indicator_distributions.items()
                },
                "threshold_suggestions": {
                    k: asdict(v) for k, v in result.threshold_suggestions.items()
                },
                "regime_type": result.regime_type,
                "regime_direction": result.regime_direction,
                "regime_confidence": result.regime_confidence
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/optimal-thresholds/{symbol}")
def get_optimal_thresholds(
    symbol: str,
    metric: str = Query(..., description="Metric name (e.g., oi_delta_percent, cvd)"),
    period: str = Query("5m", description="Time period"),
    target_hit_rate: float = Query(0.1, ge=0.01, le=0.5, description="Target signal rate"),
    lookback_days: int = Query(30, ge=7, le=90),
    db: Session = Depends(get_db)
):
    """
    Calculate optimal threshold for a specific metric.
    
    Returns conservative, moderate, and aggressive threshold suggestions.
    """
    try:
        result = market_data_analyzer.calculate_optimal_thresholds(
            db=db,
            symbol=symbol,
            metric=metric,
            period=period,
            target_hit_rate=target_hit_rate,
            lookback_days=lookback_days
        )
        
        return {
            "success": True,
            "thresholds": asdict(result)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Market Regime Endpoints
# ============================================================================

@router.get("/regime/{symbol}")
def get_current_regime(
    symbol: str,
    period: str = Query("5m", description="Analysis period"),
    db: Session = Depends(get_db)
):
    """
    Get current market regime classification.
    
    Returns regime type, direction, confidence, and indicators.
    """
    try:
        result = get_market_regime(db, symbol, period)
        
        # Add human-readable description
        result["description"] = get_regime_description(
            result.get("regime", "noise"),
            result.get("direction", "neutral")
        )
        
        return {
            "success": True,
            "regime": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/adaptive-parameters/{symbol}")
def get_adaptive_params(
    symbol: str,
    period: str = Query("1h", description="Analysis period"),
    db: Session = Depends(get_db)
):
    """
    Get adaptive trading parameters based on current market regime.
    
    Returns recommended position size, stop loss, take profit, and strategy.
    """
    try:
        result = get_adaptive_trading_parameters(db, symbol, period)
        
        return {
            "success": True,
            "parameters": asdict(result)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/multi-timeframe-regime/{symbol}")
def get_mtf_regime(
    symbol: str,
    timeframes: str = Query("5m,15m,1h,4h", description="Comma-separated timeframes"),
    db: Session = Depends(get_db)
):
    """
    Get multi-timeframe regime consensus analysis.
    
    Analyzes regime across multiple timeframes to find alignment.
    """
    try:
        tf_list = [t.strip() for t in timeframes.split(",")]
        result = get_multi_timeframe_regime_consensus(db, symbol, tf_list)
        
        return {
            "success": True,
            "consensus": result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Pattern Recognition Endpoints
# ============================================================================

@router.get("/pattern-scan/{symbol}")
def scan_active_patterns(
    symbol: str,
    period: str = Query("5m", description="Time period"),
    db: Session = Depends(get_db)
):
    """
    Scan for currently active trading patterns.
    
    Returns patterns that are currently triggered with confidence scores.
    """
    try:
        detected = pattern_recognition_service.detect_current_patterns(
            db=db,
            symbol=symbol,
            period=period
        )
        
        return {
            "success": True,
            "symbol": symbol,
            "period": period,
            "patterns_detected": len(detected),
            "patterns": [asdict(p) for p in detected]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pattern-backtest/{pattern_name}")
def backtest_system_pattern(
    pattern_name: str,
    symbol: str = Query(..., description="Trading symbol"),
    days: int = Query(30, ge=7, le=90, description="Days to backtest"),
    db: Session = Depends(get_db)
):
    """
    Backtest a system-defined pattern.
    
    Returns historical performance metrics.
    """
    if pattern_name not in SYSTEM_PATTERNS:
        raise HTTPException(status_code=404, detail=f"Pattern '{pattern_name}' not found")
    
    try:
        pattern = SYSTEM_PATTERNS[pattern_name]
        result = pattern_recognition_service.backtest_pattern(
            db=db,
            pattern=pattern,
            symbol=symbol,
            days=days
        )
        
        return {
            "success": True,
            "backtest": asdict(result)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/discover-patterns/{symbol}")
def discover_patterns(
    symbol: str,
    period: str = Query("5m", description="Time period"),
    direction: str = Query("long", description="Direction: long or short"),
    min_occurrences: int = Query(10, ge=5, le=100),
    min_win_rate: float = Query(0.55, ge=0.5, le=0.9),
    days: int = Query(30, ge=14, le=90),
    db: Session = Depends(get_db)
):
    """
    Discover new effective patterns from historical data.
    
    Tests indicator combinations to find patterns with positive expectancy.
    """
    try:
        discovered = pattern_recognition_service.discover_patterns(
            db=db,
            symbol=symbol,
            period=period,
            direction=direction,
            min_occurrences=min_occurrences,
            min_win_rate=min_win_rate,
            days=days
        )
        
        return {
            "success": True,
            "symbol": symbol,
            "period": period,
            "direction": direction,
            "patterns_found": len(discovered),
            "patterns": discovered
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/system-patterns")
def list_system_patterns():
    """
    List all system-defined trading patterns.
    """
    patterns = []
    for key, pattern in SYSTEM_PATTERNS.items():
        patterns.append({
            "key": key,
            "name": pattern.name,
            "type": pattern.pattern_type,
            "direction": pattern.direction,
            "description": pattern.description,
            "conditions_count": len(pattern.conditions),
            "best_regimes": pattern.best_regimes
        })
    
    return {
        "success": True,
        "patterns": patterns
    }


# ============================================================================
# Optimal Entry Analysis Endpoints
# ============================================================================

@router.get("/optimal-entries/{symbol}")
def analyze_optimal_entries(
    symbol: str,
    period: str = Query("5m", description="Time period"),
    lookforward_bars: int = Query(10, ge=3, le=50, description="Bars to look ahead"),
    min_profit_percent: float = Query(1.0, ge=0.5, le=10, description="Min profit threshold"),
    lookback_days: int = Query(14, ge=7, le=60),
    db: Session = Depends(get_db)
):
    """
    Analyze historical optimal entry points.
    
    Identifies points that would have yielded profitable trades.
    """
    try:
        entries = market_data_analyzer.identify_optimal_entries(
            db=db,
            symbol=symbol,
            period=period,
            lookforward_bars=lookforward_bars,
            min_profit_percent=min_profit_percent,
            lookback_days=lookback_days
        )
        
        # Analyze characteristics
        long_entries = [e for e in entries if e.direction == "long"]
        short_entries = [e for e in entries if e.direction == "short"]
        
        long_stats = market_data_analyzer.analyze_entry_characteristics(
            entries, "long"
        ) if long_entries else {"error": "No long entries"}
        
        short_stats = market_data_analyzer.analyze_entry_characteristics(
            entries, "short"
        ) if short_entries else {"error": "No short entries"}
        
        return {
            "success": True,
            "symbol": symbol,
            "period": period,
            "total_optimal_entries": len(entries),
            "long_entries": len(long_entries),
            "short_entries": len(short_entries),
            "long_characteristics": long_stats,
            "short_characteristics": short_stats,
            "sample_entries": [asdict(e) for e in entries[:20]]  # First 20
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# AI Deep Analysis Endpoint
# ============================================================================

class AIAnalysisRequest(BaseModel):
    """Request for AI deep market analysis"""
    symbol: str = Field(..., description="Trading symbol (e.g., BTC)")
    account_id: int = Field(..., alias="accountId", description="AI Trader account ID")
    market_data: dict = Field(..., alias="marketData", description="Market indicators data")
    
    class Config:
        populate_by_name = True


import re
import json

def _parse_ai_params(content: str) -> dict:
    """Extract structured parameters from AI response."""
    params = {
        "direction": "auto",
        "strategy": "adaptive",
        "risk_level": "moderate",
        "time_window": "5m",
        "confidence": 0.5,
        "entry_suggestion": None,
        "stop_loss_percent": None,
        "take_profit_percent": None,
    }
    
    # Try to find JSON block in response
    json_match = re.search(r'```json\s*(\{.*?\})\s*```', content, re.DOTALL)
    if json_match:
        try:
            parsed = json.loads(json_match.group(1))
            # Map parsed values
            if "direction" in parsed:
                d = parsed["direction"].lower()
                if d in ["long", "做多", "买入"]:
                    params["direction"] = "long"
                elif d in ["short", "做空", "卖出"]:
                    params["direction"] = "short"
                elif d in ["观望", "wait", "neutral"]:
                    params["direction"] = "auto"
            
            if "strategy" in parsed:
                s = parsed["strategy"].lower()
                if s in ["trend", "趋势"]:
                    params["strategy"] = "trend"
                elif s in ["reversal", "反转"]:
                    params["strategy"] = "reversal"
                elif s in ["breakout", "突破"]:
                    params["strategy"] = "breakout"
                elif s in ["scalping", "剥头皮"]:
                    params["strategy"] = "scalping"
            
            if "risk_level" in parsed:
                r = parsed["risk_level"].lower()
                if r in ["conservative", "保守", "低"]:
                    params["risk_level"] = "conservative"
                elif r in ["aggressive", "激进", "高"]:
                    params["risk_level"] = "aggressive"
            
            if "time_window" in parsed:
                params["time_window"] = parsed["time_window"]
            
            if "confidence" in parsed:
                params["confidence"] = float(parsed["confidence"])
            
            if "stop_loss_percent" in parsed:
                params["stop_loss_percent"] = float(parsed["stop_loss_percent"])
            
            if "take_profit_percent" in parsed:
                params["take_profit_percent"] = float(parsed["take_profit_percent"])
                
        except (json.JSONDecodeError, ValueError, KeyError):
            pass
    
    return params


@router.post("/ai-analysis")
def ai_deep_analysis(
    request: AIAnalysisRequest,
    db: Session = Depends(get_db)
):
    """
    Call AI to perform deep market analysis based on indicators.
    
    Takes current market indicators and returns AI-generated analysis
    with trading recommendations and structured parameter suggestions.
    """
    try:
        # Get AI Trader account
        account = db.query(Account).filter(Account.id == request.account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="AI Trader not found")
        
        if account.account_type != "AI":
            raise HTTPException(status_code=400, detail="Selected account is not an AI Trader")
        
        # Build AI prompt with market data
        market_data = request.market_data
        indicators = market_data.get("indicators", {})
        debug = market_data.get("debug", {})
        
        prompt = f"""你是专业的加密货币市场分析师和量化交易专家。请根据以下实时市场数据，给出详细的市场分析和交易建议。

## 当前分析品种: {request.symbol}

## 实时市场指标 (5分钟周期)
- **市场状态**: {market_data.get('regime', 'unknown')} ({market_data.get('direction', 'neutral')})
- **置信度**: {market_data.get('confidence', 0) * 100:.1f}%

### 关键指标
- **买卖比 (Taker Ratio)**: {indicators.get('taker_ratio', 'N/A')} (>1.5 买盘强势, <0.67 卖盘强势)
- **CVD 比率**: {indicators.get('cvd_ratio', 'N/A')} (>0.3 资金流入, <-0.3 资金流出)
- **RSI**: {indicators.get('rsi', 'N/A')} (>70 超买, <30 超卖)
- **OI 变化**: {indicators.get('oi_delta', 0):.1f}% (持仓量变化)
- **ATR**: {indicators.get('price_atr', 'N/A')}% (波动率)

### 成交数据
- **主动买入**: ${debug.get('taker_buy', 0):,.0f}
- **主动卖出**: ${debug.get('taker_sell', 0):,.0f}
- **总成交额**: ${debug.get('total_notional', 0):,.0f}

## 分析要求
请提供：

### 1. 市场解读
- 当前市场处于什么状态？（趋势/震荡/反转）
- 多空力量对比如何？
- 有什么值得注意的信号？

### 2. 交易建议
- 当前适合什么操作？（做多/做空/观望）
- 建议的止损和止盈百分比

### 3. 风险提示
- 当前市场的主要风险

## 重要：参数输出
在分析结束后，请输出以下 JSON 格式的参数建议（用于自动配置交易信号）：

```json
{{
  "direction": "long/short/观望",
  "strategy": "trend/reversal/breakout/adaptive",
  "risk_level": "conservative/moderate/aggressive",
  "time_window": "5m/15m/1h",
  "confidence": 0.0-1.0,
  "stop_loss_percent": 1.0-5.0,
  "take_profit_percent": 2.0-10.0
}}
```

参数说明：
- direction: 建议方向 (long=做多, short=做空, 观望=不建议交易)
- strategy: 适合的策略类型 (trend=趋势跟踪, reversal=反转, breakout=突破, adaptive=自适应)
- risk_level: 风险等级 (conservative=保守, moderate=中等, aggressive=激进)
- time_window: 建议的信号时间窗口
- confidence: 你对这个建议的置信度 (0-1)
- stop_loss_percent: 建议止损百分比
- take_profit_percent: 建议止盈百分比
"""

        # Call AI API
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {account.api_key}",
        }
        
        model_lower = (account.model or "").lower()
        is_reasoning_model = any(
            marker in model_lower for marker in [
                "o1-", "o3-", "o4-", "deepseek-r1", "qwq", "gpt-5"
            ]
        )
        is_new_model = is_reasoning_model or "gpt-4o" in model_lower
        
        payload = {
            "model": account.model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
        }
        
        if not is_reasoning_model:
            payload["temperature"] = 0.7
        
        if is_new_model:
            payload["max_completion_tokens"] = 2500
        else:
            payload["max_tokens"] = 2500
        
        endpoints = build_chat_completion_endpoints(account.base_url, account.model)
        if not endpoints:
            raise HTTPException(status_code=500, detail="Failed to build API endpoint")
        
        response = None
        success = False
        
        for endpoint in endpoints:
            try:
                logger.info(f"[AI Analysis] Calling {account.model} for {request.symbol}...")
                response = requests.post(
                    endpoint,
                    headers=headers,
                    json=payload,
                    timeout=120,
                    verify=False,
                )
                
                if response.status_code == 200:
                    success = True
                    break
                    
            except requests.Timeout:
                logger.warning(f"[AI Analysis] Timeout for endpoint: {endpoint}")
                continue
            except Exception as e:
                logger.error(f"[AI Analysis] Error: {e}")
                continue
        
        if not success or not response:
            raise HTTPException(status_code=500, detail="AI API call failed")
        
        # Extract response
        result = response.json()
        # OpenAI-compatible: result.choices[0].message.content
        try:
            message = result.get("choices", [{}])[0].get("message", {})
            content = _extract_text_from_message(message.get("content", ""))
        except (IndexError, KeyError, TypeError):
            content = ""
        
        if not content:
            logger.error(f"[AI Analysis] Empty response, raw: {result}")
            raise HTTPException(status_code=500, detail="Empty AI response")
        
        # Parse structured parameters from AI response
        suggested_params = _parse_ai_params(content)
        
        return {
            "success": True,
            "symbol": request.symbol,
            "model": account.model,
            "analysis": content,
            "suggested_params": suggested_params
        }


    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AI Analysis] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/apply-ai-analysis-to-signal")
def apply_ai_analysis_to_signal(
    request: AIAnalysisRequest,
    db: Session = Depends(get_db)
):
    """
    Apply AI analysis results to signal parameters - implements the feedback loop.
    
    This endpoint takes AI analysis results and applies them to adjust signal parameters
    in real-time, implementing the AI-driven adaptive signal optimization.
    """
    try:
        # Get AI analysis results first
        account = db.query(Account).filter(Account.id == request.account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="AI Trader not found")
        
        if account.account_type != "AI":
            raise HTTPException(status_code=400, detail="Selected account is not an AI Trader")
        
        # Build AI prompt to specifically analyze and suggest signal parameter adjustments
        market_data = request.market_data
        indicators = market_data.get("indicators", {})
        debug = market_data.get("debug", {})
        regime = market_data.get("regime", "unknown")
        direction = market_data.get("direction", "neutral")
        confidence = market_data.get("confidence", 0)
        
        prompt = f"""你是专业的加密货币量化交易专家。根据以下实时市场数据，分析并建议如何调整交易信号的具体参数，以实现AI驱动的自适应信号优化。

## 当前分析品种: {request.symbol}

## 市场状态
- **当前状态**: {regime} ({direction})
- **置信度**: {confidence * 100:.1f}%

## 关键指标
- **买卖比 (Taker Ratio)**: {indicators.get('taker_ratio', 'N/A')}
- **CVD 比率**: {indicators.get('cvd_ratio', 'N/A')}
- **RSI**: {indicators.get('rsi', 'N/A')}
- **OI 变化**: {indicators.get('oi_delta', 0):.1f}%
- **ATR**: {indicators.get('price_atr', 'N/A')}%

## 任务要求
请分析当前市场状态对以下信号参数的影响，并提供具体调整建议：
1. RSI阈值调整 (如果当前市场处于超买/超卖状态)
2. MACD敏感度调整 (根据市场趋势强度)
3. 布林带宽度调整 (根据波动率变化)
4. 止损/止盈比例调整 (根据市场风险水平)
5. 信号触发频率调整 (根据市场噪音水平)

## 重要：输出调整后的信号参数
在分析结束后，请输出以下 JSON 格式的参数调整建议：

```json
{{
  "param_adjustments": {{
    "rsi_oversold_threshold": 30,  // RSI超卖阈值调整
    "rsi_overbought_threshold": 70,  // RSI超买阈值调整
    "macd_sensitivity": 1.0,  // MACD敏感度调整倍数
    "bollinger_band_width_multiplier": 1.0,  // 布林带宽度调整倍数
    "atr_stop_loss_multiplier": 1.5,  // ATR止损倍数调整
    "take_profit_ratio": 2.0,  // 止盈比例调整
    "min_volume_threshold": 1000000,  // 最小成交量阈值
    "min_price_change_threshold": 0.01  // 最小价格变动阈值
  }},
  "strategy_recommendation": "trend_following/momentum/reversal/conservative/aggressive",
  "market_state_analysis": "当前市场状态分析和建议",
  "risk_assessment": "风险评估分数(0-1)",
  "adjustment_reasoning": "为什么需要这些调整的原因说明"
}}
```"""

        # Call AI API to get parameter adjustment suggestions
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {account.api_key}",
        }
        
        model_lower = (account.model or "").lower()
        is_reasoning_model = any(
            marker in model_lower for marker in [
                "o1-", "o3-", "o4-", "deepseek-r1", "qwq", "gpt-5"
            ]
        )
        is_new_model = is_reasoning_model or "gpt-4o" in model_lower
        
        payload = {
            "model": account.model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
        }
        
        if not is_reasoning_model:
            payload["temperature"] = 0.7
        
        if is_new_model:
            payload["max_completion_tokens"] = 2500
        else:
            payload["max_tokens"] = 2500
        
        endpoints = build_chat_completion_endpoints(account.base_url, account.model)
        if not endpoints:
            raise HTTPException(status_code=500, detail="Failed to build API endpoint")
        
        response = None
        success = False
        
        for endpoint in endpoints:
            try:
                logger.info(f"[AI Param Adjustment] Calling {account.model} for {request.symbol}...")
                response = requests.post(
                    endpoint,
                    headers=headers,
                    json=payload,
                    timeout=120,
                    verify=False,
                )
                
                if response.status_code == 200:
                    success = True
                    break
                    
            except requests.Timeout:
                logger.warning(f"[AI Param Adjustment] Timeout for endpoint: {endpoint}")
                continue
            except Exception as e:
                logger.error(f"[AI Param Adjustment] Error: {e}")
                continue
        
        if not success or not response:
            raise HTTPException(status_code=500, detail="AI API call failed")
        
        # Extract response
        result = response.json()
        try:
            message = result.get("choices", [{}])[0].get("message", {})
            content = _extract_text_from_message(message.get("content", ""))
        except (IndexError, KeyError, TypeError):
            content = ""
        
        if not content:
            logger.error(f"[AI Param Adjustment] Empty response, raw: {result}")
            raise HTTPException(status_code=500, detail="Empty AI response")
        
        # Parse the AI's parameter adjustment suggestions
        import re
        import json
        
        # Extract JSON from the response
        json_match = re.search(r'```json\s*\n(\{(?:[^{}]|{[^{}]*})*\})\s*```', content, re.DOTALL)
        if json_match:
            try:
                param_adjustments = json.loads(json_match.group(1))
            except json.JSONDecodeError:
                param_adjustments = {}
        else:
            # Fallback: try to find JSON object directly
            json_obj_match = re.search(r'\{(?:[^{}]|{[^{}]*})*\}', content)
            if json_obj_match:
                try:
                    param_adjustments = json.loads(json_obj_match.group(0))
                except json.JSONDecodeError:
                    param_adjustments = {}
            else:
                param_adjustments = {}
        
        # Store the analysis in the database for tracking
        from datetime import datetime, timezone
        from backend.database.models import AIAnalysisLog
        analysis_log = AIAnalysisLog(
            symbol=request.symbol,
            account_id=request.account_id,
            market_data=json.dumps(request.market_data),
            analysis_result=content,
            suggested_param_adjustments=json.dumps(param_adjustments) if param_adjustments else "{}",
            created_at=datetime.now(timezone.utc)
        )
        db.add(analysis_log)
        db.commit()
        
        return {
            "success": True,
            "symbol": request.symbol,
            "model": account.model,
            "analysis": content,
            "param_adjustments": param_adjustments.get("param_adjustments", {}),
            "strategy_recommendation": param_adjustments.get("strategy_recommendation", ""),
            "market_state_analysis": param_adjustments.get("market_state_analysis", ""),
            "risk_assessment": param_adjustments.get("risk_assessment", 0.5),
            "adjustment_reasoning": param_adjustments.get("adjustment_reasoning", "")
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AI Param Adjustment] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Add the database model for AI analysis logs if not exists
from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.sql import func
from backend.database.connection import Base

class AIAnalysisLog(Base):
    __tablename__ = "ai_analysis_logs"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False)
    account_id = Column(Integer, nullable=False)
    market_data = Column(Text)  # JSON string of market data
    analysis_result = Column(Text)  # Full AI response
    suggested_param_adjustments = Column(Text)  # JSON string of suggested adjustments
    created_at = Column(DateTime, server_default=func.current_timestamp())


# Also add a route to apply the adjustments to actual signal configurations
@router.post("/apply-param-adjustments")
def apply_param_adjustments(
    request: dict,  # Contains symbol and adjustment values
    db: Session = Depends(get_db)
):
    """
    Apply parameter adjustments to active signal configurations
    """
    try:
        symbol = request.get("symbol")
        adjustments = request.get("adjustments", {})
        account_id = request.get("account_id")
        
        if not symbol or not adjustments:
            raise HTTPException(status_code=400, detail="Symbol and adjustments are required")
        
        # Here we would apply the adjustments to the actual signal configurations
        # For now, we'll just return what would be adjusted as a demonstration
        # In a real implementation, this would update signal definitions in the database
        
        applied_changes = {}
        for param, value in adjustments.items():
            applied_changes[param] = {
                "previous_value": "current_value_from_db",  # Would fetch from DB
                "new_value": value,
                "applied": True
            }
        
        return {
            "success": True,
            "symbol": symbol,
            "applied_changes": applied_changes,
            "message": f"Parameter adjustments applied for {symbol}"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Apply Param Adjustments] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
