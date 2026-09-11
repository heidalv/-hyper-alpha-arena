"""Signal system API routes"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.database.connection import SessionLocal
from backend.schemas.signal import (
    SignalDefinitionCreate,
    SignalDefinitionUpdate,
    SignalDefinitionResponse,
    SignalPoolCreate,
    SignalPoolUpdate,
    SignalPoolResponse,
    SignalListResponse,
    SignalTriggerLogResponse,
    SignalTriggerLogsResponse,
)

router = APIRouter(prefix="/api/signals", tags=["Signal System"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============ Signal Definitions ============

@router.get("", response_model=SignalListResponse)
@router.get("/", response_model=SignalListResponse)
def list_signals(db: Session = Depends(get_db)) -> SignalListResponse:
    """List all signal definitions and pools"""
    import json

    signals_result = db.execute(text("""
        SELECT id, signal_name, description, trigger_condition, enabled, created_at, updated_at
        FROM signal_definitions ORDER BY id
    """))
    signals = []
    for row in signals_result:
        # Parse trigger_condition from JSON string if needed
        trigger_condition = row[3]
        if isinstance(trigger_condition, str):
            trigger_condition = json.loads(trigger_condition)
        signals.append(SignalDefinitionResponse(
            id=row[0], signal_name=row[1], description=row[2],
            trigger_condition=trigger_condition, enabled=row[4],
            created_at=row[5], updated_at=row[6]
        ))

    pools_result = db.execute(text("""
        SELECT id, pool_name, signal_ids, symbols, enabled, created_at, logic
        FROM signal_pools ORDER BY id
    """))
    pools = []
    for row in pools_result:
        # Parse JSON fields if they are strings
        signal_ids = row[2]
        if isinstance(signal_ids, str):
            signal_ids = json.loads(signal_ids)
        symbols = row[3]
        if isinstance(symbols, str):
            symbols = json.loads(symbols)
        pools.append(SignalPoolResponse(
            id=row[0], pool_name=row[1], signal_ids=signal_ids or [],
            symbols=symbols or [], enabled=row[4], created_at=row[5],
            logic=row[6] or "OR"
        ))

    return SignalListResponse(signals=signals, pools=pools)


@router.get("/definitions", response_model=List[SignalDefinitionResponse])
def list_signal_definitions(db: Session = Depends(get_db)):
    """List all signal definitions"""
    import json

    result = db.execute(text("""
        SELECT id, signal_name, description, trigger_condition, enabled, created_at, updated_at
        FROM signal_definitions ORDER BY id
    """))
    definitions = []
    for row in result:
        trigger_condition = row[3]
        if isinstance(trigger_condition, str):
            trigger_condition = json.loads(trigger_condition)
        definitions.append(SignalDefinitionResponse(
            id=row[0], signal_name=row[1], description=row[2],
            trigger_condition=trigger_condition, enabled=row[4],
            created_at=row[5], updated_at=row[6]
        ))
    return definitions


@router.get("/pools", response_model=List[SignalPoolResponse])
def list_signal_pools(db: Session = Depends(get_db)):
    """List all signal pools"""
    import json

    result = db.execute(text("""
        SELECT id, pool_name, signal_ids, symbols, enabled, created_at, logic
        FROM signal_pools ORDER BY id
    """))
    pools = []
    for row in result:
        signal_ids = row[2]
        if isinstance(signal_ids, str):
            signal_ids = json.loads(signal_ids)
        symbols = row[3]
        if isinstance(symbols, str):
            symbols = json.loads(symbols)
        pools.append(SignalPoolResponse(
            id=row[0], pool_name=row[1], signal_ids=signal_ids or [],
            symbols=symbols or [], enabled=row[4], created_at=row[5],
            logic=row[6] or "OR"
        ))
    return pools


@router.post("/definitions", response_model=SignalDefinitionResponse)
def create_signal(payload: SignalDefinitionCreate, db: Session = Depends(get_db)):
    """Create a new signal definition"""
    import json
    result = db.execute(text("""
        INSERT INTO signal_definitions (signal_name, description, trigger_condition, enabled)
        VALUES (:name, :desc, :condition, :enabled)
        RETURNING id, signal_name, description, trigger_condition, enabled, created_at, updated_at
    """), {
        "name": payload.signal_name,
        "desc": payload.description,
        "condition": json.dumps(payload.trigger_condition),
        "enabled": payload.enabled
    })
    db.commit()
    row = result.fetchone()
    trigger_condition = row[3]
    if isinstance(trigger_condition, str):
        trigger_condition = json.loads(trigger_condition)
    return SignalDefinitionResponse(
        id=row[0], signal_name=row[1], description=row[2],
        trigger_condition=trigger_condition, enabled=row[4],
        created_at=row[5], updated_at=row[6]
    )


@router.get("/definitions/{signal_id}", response_model=SignalDefinitionResponse)
def get_signal(signal_id: int, db: Session = Depends(get_db)):
    """Get a signal definition by ID"""
    import json
    result = db.execute(text("""
        SELECT id, signal_name, description, trigger_condition, enabled, created_at, updated_at
        FROM signal_definitions WHERE id = :id
    """), {"id": signal_id})
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Signal not found")
    trigger_condition = row[3]
    if isinstance(trigger_condition, str):
        trigger_condition = json.loads(trigger_condition)
    return SignalDefinitionResponse(
        id=row[0], signal_name=row[1], description=row[2],
        trigger_condition=trigger_condition, enabled=row[4],
        created_at=row[5], updated_at=row[6]
    )


@router.put("/definitions/{signal_id}", response_model=SignalDefinitionResponse)
def update_signal(signal_id: int, payload: SignalDefinitionUpdate, db: Session = Depends(get_db)):
    """Update a signal definition"""
    import json
    # Build dynamic update query
    updates = []
    params = {"id": signal_id}
    if payload.signal_name is not None:
        updates.append("signal_name = :name")
        params["name"] = payload.signal_name
    if payload.description is not None:
        updates.append("description = :desc")
        params["desc"] = payload.description
    if payload.trigger_condition is not None:
        updates.append("trigger_condition = :condition")
        params["condition"] = json.dumps(payload.trigger_condition)
    if payload.enabled is not None:
        updates.append("enabled = :enabled")
        params["enabled"] = payload.enabled

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates.append("updated_at = CURRENT_TIMESTAMP")
    query = f"UPDATE signal_definitions SET {', '.join(updates)} WHERE id = :id RETURNING *"
    result = db.execute(text(query), params)
    db.commit()
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Signal not found")
    trigger_condition = row[3]
    if isinstance(trigger_condition, str):
        trigger_condition = json.loads(trigger_condition)
    return SignalDefinitionResponse(
        id=row[0], signal_name=row[1], description=row[2],
        trigger_condition=trigger_condition, enabled=row[4],
        created_at=row[5], updated_at=row[6]
    )


@router.delete("/definitions/{signal_id}")
def delete_signal(signal_id: int, db: Session = Depends(get_db)):
    """Delete a signal definition"""
    result = db.execute(text("DELETE FROM signal_definitions WHERE id = :id"), {"id": signal_id})
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Signal not found")
    return {"message": "Signal deleted successfully"}


# ============ Signal Pools ============

@router.post("/pools", response_model=SignalPoolResponse)
def create_pool(payload: SignalPoolCreate, db: Session = Depends(get_db)):
    """Create a new signal pool"""
    import json
    result = db.execute(text("""
        INSERT INTO signal_pools (pool_name, signal_ids, symbols, enabled, logic)
        VALUES (:name, :signal_ids, :symbols, :enabled, :logic)
        RETURNING id, pool_name, signal_ids, symbols, enabled, created_at, logic
    """), {
        "name": payload.pool_name,
        "signal_ids": json.dumps(payload.signal_ids),
        "symbols": json.dumps(payload.symbols),
        "enabled": payload.enabled,
        "logic": payload.logic
    })
    db.commit()
    row = result.fetchone()
    signal_ids = row[2]
    if isinstance(signal_ids, str):
        signal_ids = json.loads(signal_ids)
    symbols = row[3]
    if isinstance(symbols, str):
        symbols = json.loads(symbols)
    return SignalPoolResponse(
        id=row[0], pool_name=row[1], signal_ids=signal_ids or [],
        symbols=symbols or [], enabled=row[4], created_at=row[5],
        logic=row[6] or "OR"
    )


@router.get("/pools/{pool_id}", response_model=SignalPoolResponse)
def get_pool(pool_id: int, db: Session = Depends(get_db)):
    """Get a signal pool by ID"""
    import json
    result = db.execute(text("""
        SELECT id, pool_name, signal_ids, symbols, enabled, created_at, logic
        FROM signal_pools WHERE id = :id
    """), {"id": pool_id})
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Pool not found")
    signal_ids = row[2]
    if isinstance(signal_ids, str):
        signal_ids = json.loads(signal_ids)
    symbols = row[3]
    if isinstance(symbols, str):
        symbols = json.loads(symbols)
    return SignalPoolResponse(
        id=row[0], pool_name=row[1], signal_ids=signal_ids or [],
        symbols=symbols or [], enabled=row[4], created_at=row[5],
        logic=row[6] or "OR"
    )


@router.put("/pools/{pool_id}", response_model=SignalPoolResponse)
def update_pool(pool_id: int, payload: SignalPoolUpdate, db: Session = Depends(get_db)):
    """Update a signal pool"""
    import json
    updates = []
    params = {"id": pool_id}
    if payload.pool_name is not None:
        updates.append("pool_name = :name")
        params["name"] = payload.pool_name
    if payload.signal_ids is not None:
        updates.append("signal_ids = :signal_ids")
        params["signal_ids"] = json.dumps(payload.signal_ids)
    if payload.symbols is not None:
        updates.append("symbols = :symbols")
        params["symbols"] = json.dumps(payload.symbols)
    if payload.enabled is not None:
        updates.append("enabled = :enabled")
        params["enabled"] = payload.enabled
    if payload.logic is not None:
        updates.append("logic = :logic")
        params["logic"] = payload.logic

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    query = f"UPDATE signal_pools SET {', '.join(updates)} WHERE id = :id RETURNING id, pool_name, signal_ids, symbols, enabled, created_at, logic"
    result = db.execute(text(query), params)
    db.commit()
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Pool not found")
    signal_ids = row[2]
    if isinstance(signal_ids, str):
        signal_ids = json.loads(signal_ids)
    symbols = row[3]
    if isinstance(symbols, str):
        symbols = json.loads(symbols)
    return SignalPoolResponse(
        id=row[0], pool_name=row[1], signal_ids=signal_ids or [],
        symbols=symbols or [], enabled=row[4], created_at=row[5],
        logic=row[6] or "OR"
    )


@router.delete("/pools/{pool_id}")
def delete_pool(pool_id: int, db: Session = Depends(get_db)):
    """Delete a signal pool"""
    result = db.execute(text("DELETE FROM signal_pools WHERE id = :id"), {"id": pool_id})
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Pool not found")
    return {"message": "Pool deleted successfully"}


@router.get("/pools/{pool_id}/stats")
def get_pool_stats(
    pool_id: int,
    days: int = Query(7, le=30, description="Days of history to analyze"),
    db: Session = Depends(get_db)
):
    """
    Get trigger statistics for a signal pool.
    Returns total triggers, daily triggers, and per-signal breakdown.
    """
    import json
    from datetime import datetime, timedelta, timezone

    # Verify pool exists
    pool_result = db.execute(text("""
        SELECT id, pool_name, signal_ids FROM signal_pools WHERE id = :id
    """), {"id": pool_id})
    pool_row = pool_result.fetchone()
    if not pool_row:
        raise HTTPException(status_code=404, detail="Pool not found")

    signal_ids = pool_row[2]
    if isinstance(signal_ids, str):
        signal_ids = json.loads(signal_ids)

    # Calculate date range
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # Get total triggers for the pool
    total_result = db.execute(text("""
        SELECT COUNT(*) FROM signal_trigger_logs
        WHERE pool_id = :pool_id AND triggered_at >= :start_date
    """), {"pool_id": pool_id, "start_date": start_date})
    total_triggers = total_result.scalar() or 0

    # Get today's triggers
    today_result = db.execute(text("""
        SELECT COUNT(*) FROM signal_trigger_logs
        WHERE pool_id = :pool_id AND triggered_at >= :today_start
    """), {"pool_id": pool_id, "today_start": today_start})
    triggers_today = today_result.scalar() or 0

    # Get daily distribution
    distribution_result = db.execute(text("""
        SELECT DATE(triggered_at) as trigger_date, COUNT(*) as count
        FROM signal_trigger_logs
        WHERE pool_id = :pool_id AND triggered_at >= :start_date
        GROUP BY DATE(triggered_at)
        ORDER BY trigger_date
    """), {"pool_id": pool_id, "start_date": start_date})
    trigger_distribution = {str(row[0]): row[1] for row in distribution_result}

    # Get per-signal stats
    signal_stats = []
    if signal_ids:
        for sig_id in signal_ids:
            sig_result = db.execute(text("""
                SELECT sd.signal_name, COUNT(stl.id) as count
                FROM signal_definitions sd
                LEFT JOIN signal_trigger_logs stl ON stl.signal_id = sd.id
                    AND stl.triggered_at >= :start_date
                WHERE sd.id = :signal_id
                GROUP BY sd.id, sd.signal_name
            """), {"signal_id": sig_id, "start_date": start_date})
            sig_row = sig_result.fetchone()
            if sig_row:
                signal_stats.append({
                    "signal_id": sig_id,
                    "signal_name": sig_row[0],
                    "trigger_count": sig_row[1] or 0
                })

    return {
        "pool_id": pool_id,
        "pool_name": pool_row[1],
        "days": days,
        "total_triggers": total_triggers,
        "triggers_today": triggers_today,
        "trigger_distribution": trigger_distribution,
        "signal_stats": signal_stats
    }


# ============ Metric Analysis ============

@router.get("/analyze")
def analyze_metric(
    symbol: str = Query(..., description="Trading symbol (e.g., BTC)"),
    metric: str = Query(..., description="Metric type (e.g., oi_delta_percent)"),
    period: str = Query("5m", description="Time period (e.g., 5m, 15m)"),
    days: int = Query(7, le=30, description="Days of history to analyze"),
    db: Session = Depends(get_db)
):
    """
    Analyze a metric and provide statistical summary with threshold suggestions.

    Returns statistics and suggested thresholds based on historical data.
    """
    from backend.services.signal_analysis_service import signal_analysis_service

    result = signal_analysis_service.analyze_metric(db, symbol, metric, period, days)
    return result


# ============ Signal Backtest Preview ============

@router.get("/backtest/{signal_id}")
def backtest_signal(
    signal_id: int,
    symbol: str = Query(..., description="Trading symbol (e.g., BTC)"),
    kline_min_ts: int = Query(None, description="Min K-line timestamp in ms (for filtering triggers)"),
    kline_max_ts: int = Query(None, description="Max K-line timestamp in ms (for filtering triggers)"),
    db: Session = Depends(get_db)
):
    """
    Backtest a signal against historical data.
    Returns only trigger points - K-lines should be fetched via /api/market/kline-with-indicators.
    """
    from backend.services.signal_backtest_service import signal_backtest_service

    try:
        result = signal_backtest_service.backtest_signal(db, signal_id, symbol, kline_min_ts, kline_max_ts)
        return result
    except Exception as e:
        logger.error(
            f"[Backtest API] EXCEPTION: signal_id={signal_id}, symbol={symbol}, "
            f"ts_range=[{kline_min_ts}, {kline_max_ts}], error={e}",
            exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Backtest failed: {str(e)}")


from pydantic import BaseModel, Field


class TempBacktestRequest(BaseModel):
    """Request for temporary signal backtest (without saving to database)"""
    symbol: str = Field(..., description="Trading symbol (e.g., BTC)")
    trigger_condition: dict = Field(..., alias="triggerCondition", description="Signal trigger condition")
    kline_min_ts: Optional[int] = Field(None, alias="klineMinTs", description="Min K-line timestamp in ms")
    kline_max_ts: Optional[int] = Field(None, alias="klineMaxTs", description="Max K-line timestamp in ms")

    class Config:
        populate_by_name = True


@router.post("/backtest-preview")
def backtest_preview(
    request: TempBacktestRequest,
    db: Session = Depends(get_db)
):
    """
    Backtest a signal configuration without saving to database.
    Used for AI signal creation preview before actually creating the signal.
    """
    from backend.services.signal_backtest_service import signal_backtest_service

    try:
        result = signal_backtest_service.backtest_temp_signal(
            db=db,
            symbol=request.symbol,
            trigger_condition=request.trigger_condition,
            kline_min_ts=request.kline_min_ts,
            kline_max_ts=request.kline_max_ts
        )
        return result
    except Exception as e:
        logger.error(
            f"[Backtest API] EXCEPTION in preview: symbol={request.symbol}, "
            f"condition={request.trigger_condition}, error={e}",
            exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Backtest preview failed: {str(e)}")


@router.get("/pool-backtest/{pool_id}")
def backtest_pool(
    pool_id: int,
    symbol: str = Query(..., description="Trading symbol (e.g., BTC)"),
    kline_min_ts: int = Query(None, description="Min K-line timestamp in ms"),
    kline_max_ts: int = Query(None, description="Max K-line timestamp in ms"),
    db: Session = Depends(get_db)
):
    """
    Backtest a signal pool against historical data.
    Combines triggers from multiple signals based on pool logic (AND/OR).
    """
    from backend.services.signal_backtest_service import signal_backtest_service

    try:
        result = signal_backtest_service.backtest_pool(db, pool_id, symbol, kline_min_ts, kline_max_ts)
        return result
    except Exception as e:
        logger.error(
            f"[Backtest API] EXCEPTION in pool: pool_id={pool_id}, symbol={symbol}, "
            f"ts_range=[{kline_min_ts}, {kline_max_ts}], error={e}",
            exc_info=True
        )
        raise HTTPException(status_code=500, detail=f"Pool backtest failed: {str(e)}")


# ============ Enhanced Backtest with Performance Analysis ============

class BacktestConfigRequest(BaseModel):
    """Backtest configuration options"""
    position_size_usd: float = Field(1000.0, description="Simulated position size in USD")
    take_profit_percent: float = Field(2.0, description="Take profit percentage")
    stop_loss_percent: float = Field(1.0, description="Stop loss percentage")
    max_hold_bars: int = Field(20, description="Max bars to hold before force exit")
    use_trailing_stop: bool = Field(False, description="Use trailing stop")
    trailing_stop_percent: float = Field(0.5, description="Trailing stop percentage")
    commission_percent: float = Field(0.04, description="Commission per trade (%)")


class PerformanceBacktestRequest(BaseModel):
    """Request for performance backtest"""
    symbol: str = Field(..., description="Trading symbol (e.g., BTC)")
    days: int = Field(7, ge=1, le=30, description="Number of days to backtest")
    config: Optional[BacktestConfigRequest] = Field(None, description="Backtest config")


class TempPerformanceBacktestRequest(BaseModel):
    """Request for temporary signal performance backtest"""
    symbol: str = Field(..., description="Trading symbol (e.g., BTC)")
    trigger_condition: dict = Field(..., alias="triggerCondition", description="Signal trigger condition")
    days: int = Field(7, ge=1, le=30, description="Number of days to backtest")
    config: Optional[BacktestConfigRequest] = Field(None, description="Backtest config")

    class Config:
        populate_by_name = True


@router.post("/backtest/{signal_id}/performance")
def backtest_signal_with_performance(
    signal_id: int,
    request: PerformanceBacktestRequest,
    db: Session = Depends(get_db)
):
    """
    Enhanced backtest with simulated trading and comprehensive performance metrics.
    
    Returns:
    - Trade list with entry/exit prices and PnL
    - Performance summary (win rate, profit factor, max drawdown, Sharpe ratio)
    - Equity curve data
    - Time distribution analysis
    """
    from backend.services.backtest_performance_service import backtest_performance_service, BacktestConfig
    
    try:
        config = None
        if request.config:
            config = BacktestConfig(
                position_size_usd=request.config.position_size_usd,
                take_profit_percent=request.config.take_profit_percent,
                stop_loss_percent=request.config.stop_loss_percent,
                max_hold_bars=request.config.max_hold_bars,
                use_trailing_stop=request.config.use_trailing_stop,
                trailing_stop_percent=request.config.trailing_stop_percent,
                commission_percent=request.config.commission_percent,
            )
        
        result = backtest_performance_service.backtest_signal_with_performance(
            db=db,
            signal_id=signal_id,
            symbol=request.symbol,
            days=request.days,
            config=config,
        )
        # 统一进化学习内核：把信号回测结果并入统一血缘账本 + RL replay（非侵入、全保护）
        _fold_backtest_into_learning_core(result, symbol=request.symbol, source="signal_backtest")
        return result
    except Exception as e:
        logger.error(f"[Performance Backtest] signal_id={signal_id}, error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Performance backtest failed: {str(e)}")


def _fold_backtest_into_learning_core(result: dict, *, symbol: str, source: str) -> None:
    """把信号回测结果折算成统一内核事件（失败静默，绝不影响回测本身）。"""
    try:
        if not isinstance(result, dict):
            return
        summary = result.get("summary") or result.get("performance") or {}
        metrics = {
            "sharpe": summary.get("sharpe_ratio") or summary.get("sharpe"),
            "win_rate": summary.get("win_rate"),
            "max_dd": summary.get("max_drawdown"),
            "profit_factor": summary.get("profit_factor"),
            "total_trades": summary.get("total_trades"),
            "total_return": summary.get("total_return"),
        }
        trades = result.get("trades") or []
        from backend.services.learning_core.backtest_loop import backtest_loop
        backtest_loop.ingest_result(
            source=source, symbol=symbol, metrics=metrics,
            trades=trades if isinstance(trades, list) else None,
            drive_optimization=False,  # 信号回测不驱动模板参数优化，仅入账本+replay
        )
    except Exception:
        pass


@router.post("/pools/{pool_id}/backtest-performance")
def backtest_pool_with_performance(
    pool_id: int,
    request: PerformanceBacktestRequest,
    db: Session = Depends(get_db)
):
    """
    Enhanced pool backtest with simulated trading and performance metrics.
    Includes per-signal contribution analysis.
    """
    from backend.services.backtest_performance_service import backtest_performance_service, BacktestConfig
    
    try:
        config = None
        if request.config:
            config = BacktestConfig(
                position_size_usd=request.config.position_size_usd,
                take_profit_percent=request.config.take_profit_percent,
                stop_loss_percent=request.config.stop_loss_percent,
                max_hold_bars=request.config.max_hold_bars,
                use_trailing_stop=request.config.use_trailing_stop,
                trailing_stop_percent=request.config.trailing_stop_percent,
                commission_percent=request.config.commission_percent,
            )
        
        result = backtest_performance_service.backtest_pool_with_performance(
            db=db,
            pool_id=pool_id,
            symbol=request.symbol,
            days=request.days,
            config=config,
        )
        return result
    except Exception as e:
        logger.error(f"[Pool Performance Backtest] pool_id={pool_id}, error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Pool performance backtest failed: {str(e)}")


@router.post("/backtest-performance-preview")
def backtest_temp_signal_with_performance(
    request: TempPerformanceBacktestRequest,
    db: Session = Depends(get_db)
):
    """
    Performance backtest for temporary signal configuration.
    Used for template preview and AI-generated signals before saving.
    """
    from backend.services.backtest_performance_service import backtest_performance_service, BacktestConfig
    
    try:
        config = None
        if request.config:
            config = BacktestConfig(
                position_size_usd=request.config.position_size_usd,
                take_profit_percent=request.config.take_profit_percent,
                stop_loss_percent=request.config.stop_loss_percent,
                max_hold_bars=request.config.max_hold_bars,
                use_trailing_stop=request.config.use_trailing_stop,
                trailing_stop_percent=request.config.trailing_stop_percent,
                commission_percent=request.config.commission_percent,
            )
        
        result = backtest_performance_service.backtest_temp_signal_with_performance(
            db=db,
            symbol=request.symbol,
            trigger_condition=request.trigger_condition,
            days=request.days,
            config=config,
        )
        return result
    except Exception as e:
        logger.error(f"[Temp Performance Backtest] symbol={request.symbol}, error={e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Performance backtest preview failed: {str(e)}")


# ============ Trigger Logs ============

@router.get("/logs", response_model=SignalTriggerLogsResponse)
def get_trigger_logs(
    pool_id: Optional[int] = Query(None),
    signal_id: Optional[int] = Query(None),
    symbol: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db)
):
    """Get signal trigger logs with optional filters"""
    conditions = []
    params = {"limit": limit}

    if pool_id is not None:
        conditions.append("pool_id = :pool_id")
        params["pool_id"] = pool_id
    if signal_id is not None:
        conditions.append("signal_id = :signal_id")
        params["signal_id"] = signal_id
    if symbol is not None:
        conditions.append("symbol = :symbol")
        params["symbol"] = symbol

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"""
        SELECT id, signal_id, pool_id, symbol, trigger_value, triggered_at, market_regime
        FROM signal_trigger_logs {where_clause}
        ORDER BY triggered_at DESC LIMIT :limit
    """
    import json
    result = db.execute(text(query), params)
    logs = []
    for row in result:
        # Parse trigger_value - ORM defines as Text, so it may be string
        trigger_val = row[4]
        if isinstance(trigger_val, str):
            try:
                trigger_val = json.loads(trigger_val)
            except json.JSONDecodeError:
                trigger_val = None
        # Parse market_regime - also stored as Text/JSON
        market_regime_val = row[6]
        if isinstance(market_regime_val, str):
            try:
                market_regime_val = json.loads(market_regime_val)
            except json.JSONDecodeError:
                market_regime_val = None
        logs.append(SignalTriggerLogResponse(
            id=row[0], signal_id=row[1], pool_id=row[2],
            symbol=row[3], trigger_value=trigger_val, triggered_at=row[5],
            market_regime=market_regime_val
        ))

    # Get total count
    count_query = f"SELECT COUNT(*) FROM signal_trigger_logs {where_clause}"
    count_params = {k: v for k, v in params.items() if k != "limit"}
    total = db.execute(text(count_query), count_params).scalar()

    return SignalTriggerLogsResponse(logs=logs, total=total)


# ============ Signal Testing & Monitoring ============

@router.get("/test/{signal_id}")
def test_signal(
    signal_id: int,
    symbol: str = Query(..., description="Symbol to test against"),
    db: Session = Depends(get_db)
):
    """
    Test a signal against current market data.
    Returns the current metric value and whether the condition is met.
    """
    import json
    from backend.services.signal_detection_service import signal_detection_service
    from backend.services.market_flow_collector import market_flow_collector

    # Get signal definition
    result = db.execute(text("""
        SELECT id, signal_name, description, trigger_condition, enabled
        FROM signal_definitions WHERE id = :id
    """), {"id": signal_id})
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Signal not found")

    # Parse trigger_condition - may be string (TEXT) or dict (JSONB)
    trigger_condition = row[3]
    if isinstance(trigger_condition, str):
        try:
            trigger_condition = json.loads(trigger_condition)
        except json.JSONDecodeError:
            trigger_condition = {}

    signal_def = {
        "id": row[0],
        "signal_name": row[1],
        "description": row[2],
        "trigger_condition": trigger_condition,
        "enabled": row[4]
    }

    # Get current market data from collector
    market_data = {
        "asset_ctx": market_flow_collector.latest_asset_ctx.get(symbol, {}),
        "orderbook": market_flow_collector.latest_orderbook.get(symbol, {}),
    }

    condition = signal_def.get("trigger_condition", {})
    metric = condition.get("metric")
    operator = condition.get("operator")
    threshold = condition.get("threshold")
    time_window = condition.get("time_window", 60)

    # Get current metric value
    current_value = signal_detection_service._get_metric_value(
        metric, symbol, market_data, time_window
    )

    # Evaluate condition
    condition_met = False
    if current_value is not None:
        condition_met = signal_detection_service._evaluate_condition(
            current_value, operator, threshold
        )

    # Get signal state
    state_key = (signal_id, symbol)
    state = signal_detection_service.signal_states.get(state_key)

    return {
        "signal_id": signal_id,
        "signal_name": signal_def["signal_name"],
        "symbol": symbol,
        "metric": metric,
        "operator": operator,
        "threshold": threshold,
        "time_window": time_window,
        "current_value": current_value,
        "condition_met": condition_met,
        "is_active": state.is_active if state else False,
        "would_trigger": condition_met and (not state or not state.is_active),
        "market_data_available": bool(market_data.get("asset_ctx")),
    }


@router.get("/states")
def get_signal_states():
    """Get current signal states for monitoring"""
    from services.signal_detection_service import signal_detection_service
    return {
        "states": signal_detection_service.get_signal_states(),
        "cache_info": {
            "pools_count": len(signal_detection_service._signal_pools_cache),
            "signals_count": len(signal_detection_service._signals_cache),
        }
    }


@router.post("/states/reset")
def reset_signal_states(
    signal_id: Optional[int] = Query(None),
    pool_id: Optional[int] = Query(None),
    symbol: Optional[str] = Query(None)
):
    """Reset signal and pool states (useful for testing)"""
    from services.signal_detection_service import signal_detection_service
    signal_detection_service.reset_state(signal_id, pool_id, symbol)
    return {"message": "Signal and pool states reset successfully"}


# ============ AI Signal Generation Chat APIs ============

from fastapi.responses import StreamingResponse
from backend.services.ai_signal_generation_service import (
    generate_signal_with_ai,
    generate_signal_with_ai_stream,
    get_signal_conversation_history,
    get_signal_conversation_messages
)
from backend.database.models import User


class AiSignalChatRequest(BaseModel):
    """Request to send a message to AI signal generation chat"""
    account_id: int = Field(..., alias="accountId")
    user_message: str = Field(..., alias="userMessage")
    conversation_id: Optional[int] = Field(None, alias="conversationId")

    class Config:
        populate_by_name = True


class AiSignalChatResponse(BaseModel):
    """Response from AI signal generation chat"""
    success: bool
    conversation_id: Optional[int] = Field(None, alias="conversationId")
    message_id: Optional[int] = Field(None, alias="messageId")
    content: Optional[str] = None
    signal_configs: Optional[List[dict]] = Field(None, alias="signalConfigs")
    error: Optional[str] = None

    class Config:
        populate_by_name = True


@router.post("/ai-chat", response_model=AiSignalChatResponse)
def ai_signal_chat(
    request: AiSignalChatRequest,
    db: Session = Depends(get_db)
) -> AiSignalChatResponse:
    """Send a message to AI signal generation assistant"""
    # Get user (default user for now)
    user = db.query(User).filter(User.username == "default").first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    result = generate_signal_with_ai(
        db=db,
        account_id=request.account_id,
        user_message=request.user_message,
        conversation_id=request.conversation_id,
        user_id=user.id
    )

    return AiSignalChatResponse(
        success=result.get("success", False),
        conversation_id=result.get("conversation_id"),
        message_id=result.get("message_id"),
        content=result.get("content"),
        signal_configs=result.get("signal_configs"),
        error=result.get("error")
    )


@router.get("/ai-conversations")
def list_ai_signal_conversations(
    limit: int = 20,
    db: Session = Depends(get_db)
) -> dict:
    """Get list of AI signal generation conversations"""
    user = db.query(User).filter(User.username == "default").first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    conversations = get_signal_conversation_history(
        db=db,
        user_id=user.id,
        limit=limit
    )

    return {"conversations": conversations}


@router.get("/ai-conversations/{conversation_id}/messages")
def get_ai_signal_conversation_messages(
    conversation_id: int,
    db: Session = Depends(get_db)
) -> dict:
    """Get all messages in a specific conversation"""
    user = db.query(User).filter(User.username == "default").first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    messages = get_signal_conversation_messages(
        db=db,
        conversation_id=conversation_id,
        user_id=user.id
    )

    if messages is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {"messages": messages}


# ============ AI Signal Generation SSE Streaming ============

@router.post("/ai-chat-stream")
def ai_signal_chat_stream(
    request: AiSignalChatRequest,
    db: Session = Depends(get_db)
):
    """
    Send a message to AI signal generation assistant with SSE streaming.

    Returns a Server-Sent Events stream with the following event types:
    - status: Progress status message
    - tool_call: Tool being called with arguments
    - tool_result: Result from tool execution
    - reasoning: AI reasoning content (for reasoning models)
    - content: AI response content chunk
    - signal_config: Parsed signal configuration
    - done: Completion with final result
    - error: Error occurred
    """
    user = db.query(User).filter(User.username == "default").first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    def event_generator():
        for event in generate_signal_with_ai_stream(
            db=db,
            account_id=request.account_id,
            user_message=request.user_message,
            conversation_id=request.conversation_id,
            user_id=user.id
        ):
            yield event

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


# ============ AI Signal Pool Creation ============

class SignalPoolConfigRequest(BaseModel):
    """Request for creating signal pool from AI-generated config"""
    name: str = Field(..., description="Pool name")
    symbol: Optional[str] = Field(None, description="Trading symbol (legacy)")
    symbols: Optional[List[str]] = Field(None, description="List of trading symbols")
    description: Optional[str] = Field(None, description="Pool description")
    logic: str = Field("AND", description="Combination logic: AND or OR")
    signals: List[dict] = Field(..., description="List of signal configurations")

    class Config:
        populate_by_name = True


@router.post("/create-pool-from-config")
def create_pool_from_config(
    request: SignalPoolConfigRequest,
    db: Session = Depends(get_db)
):
    """
    Create a signal pool from AI-generated configuration.
    Creates individual signals and combines them into a pool.
    """
    import json

    if not request.signals:
        raise HTTPException(status_code=400, detail="No signals provided")

    if len(request.signals) > 100:
        raise HTTPException(status_code=400, detail="Maximum 100 signals per pool")

    # Handle symbols: support both single 'symbol' and list 'symbols'
    target_symbols = request.symbols
    if not target_symbols and request.symbol:
        # Handle comma-separated string if AI returns "BTC,ETH"
        if "," in request.symbol:
            target_symbols = [s.strip() for s in request.symbol.split(",")]
        else:
            target_symbols = [request.symbol]
    
    if not target_symbols:
        target_symbols = [] # Allow empty symbols, user can add later

    created_signal_ids = []
    created_signals = []

    try:
        # Create each signal
        for i, sig in enumerate(request.signals):
            # Generate signal name if not provided
            sig_name = sig.get("name") or f"{request.name}_{i+1}"

            # Build trigger condition - handle taker_volume composite signal specially
            metric_name = sig.get("metric") or sig.get("indicator")

            if metric_name == "taker_volume":
                # taker_volume uses direction/ratio_threshold/volume_threshold instead of operator/threshold
                trigger_condition = {
                    "metric": metric_name,
                    "direction": sig.get("direction"),
                    "ratio_threshold": sig.get("ratio_threshold"),
                    "volume_threshold": sig.get("volume_threshold"),
                    "time_window": sig.get("time_window")
                }
                # Validate taker_volume required fields
                if not all([trigger_condition["metric"], trigger_condition["direction"],
                           trigger_condition["ratio_threshold"] is not None,
                           trigger_condition["volume_threshold"] is not None,
                           trigger_condition["time_window"]]):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Signal {i+1} (taker_volume) missing required fields (direction, ratio_threshold, volume_threshold, time_window)"
                    )
            else:
                # Standard signal with operator/threshold
                trigger_condition = {
                    "metric": metric_name,
                    "operator": sig.get("operator"),
                    "threshold": sig.get("threshold"),
                    "time_window": sig.get("time_window")
                }
                # Validate standard signal required fields
                if not all([trigger_condition["metric"], trigger_condition["operator"],
                           trigger_condition["threshold"] is not None, trigger_condition["time_window"]]):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Signal {i+1} missing required fields (metric, operator, threshold, time_window)"
                    )

            # Create signal
            result = db.execute(text("""
                INSERT INTO signal_definitions (signal_name, description, trigger_condition, enabled)
                VALUES (:name, :desc, :condition, :enabled)
                RETURNING id, signal_name, description, trigger_condition, enabled, created_at
            """), {
                "name": sig_name,
                "desc": sig.get("description") or f"Part of {request.name}",
                "condition": json.dumps(trigger_condition),
                "enabled": True
            })
            row = result.fetchone()
            created_signal_ids.append(row[0])
            created_signals.append({
                "id": row[0],
                "signal_name": row[1],
                "trigger_condition": trigger_condition
            })

        # Create the pool
        pool_result = db.execute(text("""
            INSERT INTO signal_pools (pool_name, signal_ids, symbols, enabled, logic)
            VALUES (:name, :signal_ids, :symbols, :enabled, :logic)
            RETURNING id, pool_name, signal_ids, symbols, enabled, created_at, logic
        """), {
            "name": request.name,
            "signal_ids": json.dumps(created_signal_ids),
            "symbols": json.dumps(target_symbols),
            "enabled": True,
            "logic": request.logic
        })
        pool_row = pool_result.fetchone()

        db.commit()

        return {
            "success": True,
            "pool": {
                "id": pool_row[0],
                "pool_name": pool_row[1],
                "signal_ids": created_signal_ids,
                "symbols": target_symbols,
                "logic": request.logic
            },
            "signals": created_signals
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create pool: {str(e)}")


# ══════════════════════════════════════════════════════════════
#  统一信号总线 API — 四源汇流
# ══════════════════════════════════════════════════════════════

@router.get("/unified/{symbol}")
def get_unified_signal(symbol: str, force_refresh: bool = Query(False)):
    """获取统一融合信号（因子+情报+确认+融合 四源汇流）"""
    try:
        from backend.services.signal_engine import unified_signal_bus
        result = unified_signal_bus.get_unified_signal(symbol, force_refresh=force_refresh)
        return {
            "symbol": result.symbol,
            "direction": result.direction,
            "confidence": result.confidence,
            "strength": result.strength,
            "action": result.action,
            "confluence_level": result.confluence_level,
            "source_count": result.source_count,
            "agreeing_sources": result.agreeing_sources,
            "conflicting_sources": result.conflicting_sources,
            "sources": {
                k: {
                    "source_name": v.source_name,
                    "direction": v.direction,
                    "confidence": v.confidence,
                    "strength": v.strength,
                    "action": v.action,
                    "weight": v.weight,
                }
                for k, v in result.sources.items()
            },
            "regime": result.regime,
            "reasoning": result.reasoning,
            "timestamp": result.timestamp,
        }
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


@router.get("/unified/{symbol}/detail")
def get_signal_detail(symbol: str):
    """获取各信号源详细分解（含原始数据、融合调试信息、条件触发状态）"""
    try:
        from backend.services.signal_engine import unified_signal_bus
        return unified_signal_bus.get_signal_detail(symbol)
    except Exception as e:
        return {"error": str(e), "symbol": symbol}
