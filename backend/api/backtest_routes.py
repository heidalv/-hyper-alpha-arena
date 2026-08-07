"""回测进化系统 API"""

import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database.connection import get_db
from backend.database.models import BacktestRun, BacktestTrade
from backend.services.strategy_evolver import strategy_evolver

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/backtest", tags=["Backtest"])


class EvolutionConfig(BaseModel):
    symbols: List[str] = ["BTC", "ETH"]
    timeframes: List[str] = ["1h"]
    tier: str = "mid"
    max_generations: int = 5
    population_per_gen: int = 6
    lookback_days: int = 730
    max_workers: int = 4
    engine_type: str = "live_pipeline"  # "live_pipeline" | "legacy"


class SingleBacktestRequest(BaseModel):
    template_id: str
    symbol: str = "BTC"
    timeframe: str = "1h"
    days: int = 365


@router.post("/evolution/start")
def start_evolution(config: Optional[EvolutionConfig] = None):
    """启动自动回测进化"""
    cfg = config.model_dump() if config else {}
    return strategy_evolver.start_evolution(cfg)


@router.post("/evolution/stop")
def stop_evolution():
    """停止进化"""
    return strategy_evolver.stop_evolution()


@router.get("/evolution/progress")
def get_progress():
    """获取进化进度"""
    return {
        "running": strategy_evolver.is_running,
        **strategy_evolver.progress,
    }


class SinglePipelineBacktestRequest(BaseModel):
    symbol: str = "BTC"
    timeframe: str = "1h"
    days: int = 365
    pipeline_params: Optional[dict] = None


@router.post("/single")
def run_single_backtest(request: SingleBacktestRequest):
    """对单个模板运行一次回测（旧引擎）"""
    result = strategy_evolver.run_single_backtest(
        template_id=request.template_id,
        symbol=request.symbol,
        timeframe=request.timeframe,
        days=request.days,
    )
    if not result:
        raise HTTPException(status_code=500, detail="回测失败")
    return result


@router.post("/single-pipeline")
def run_single_pipeline_backtest(request: SinglePipelineBacktestRequest,
                                  db: Session = Depends(get_db)):
    """用实盘管线引擎运行单次回测"""
    from backend.services.live_pipeline_backtest_engine import (
        LivePipelineBacktestEngine, DEFAULT_PIPELINE_PARAMS,
    )

    bars = strategy_evolver._load_bars(db, request.symbol, request.timeframe, request.days)
    if not bars or len(bars) < 50:
        raise HTTPException(status_code=400, detail="K线数据不足")

    params = {**DEFAULT_PIPELINE_PARAMS}
    if request.pipeline_params:
        params.update(request.pipeline_params)

    funding_rates = strategy_evolver._load_funding_rates(db, [request.symbol])
    fgi_series = strategy_evolver._load_fgi_series(db, request.days)

    engine = LivePipelineBacktestEngine(initial_capital=10000)
    result = engine.run(bars, params, tier="mid",
                        funding_rate_series=funding_rates, fgi_series=fgi_series)

    if result.error:
        raise HTTPException(status_code=500, detail=result.error)

    return {
        "run_id": result.run_id,
        "engine_type": "live_pipeline",
        "total_return": result.total_return,
        "annualized_return": result.annualized_return,
        "sharpe_ratio": result.sharpe_ratio,
        "max_drawdown": result.max_drawdown,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
        "total_trades": result.total_trades,
        "avg_trade_return": result.avg_trade_return,
        "final_equity": result.final_equity,
        "bars_total": result.bars_total,
        "duration_seconds": result.duration_seconds,
        "data_completeness": getattr(result, "data_completeness", None),
        "trades": [
            {
                "side": t.side,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "pnl": round(t.pnl, 2),
                "pnl_pct": round(t.pnl_pct, 4),
                "exit_reason": t.exit_reason,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
            }
            for t in result.trades[:200]
        ],
    }


@router.get("/runs")
def list_runs(template_id: Optional[str] = None, champions_only: bool = False,
              limit: int = 30, db: Session = Depends(get_db)):
    """列出回测记录"""
    q = db.query(BacktestRun)
    if template_id:
        q = q.filter(BacktestRun.template_id == template_id)
    if champions_only:
        q = q.filter(BacktestRun.is_champion == True)
    runs = q.order_by(BacktestRun.created_at.desc()).limit(limit).all()
    return [
        {
            "run_id": r.run_id,
            "template_id": r.template_id,
            "strategy_name": r.strategy_name,
            "symbol": r.symbol,
            "timeframe": r.timeframe,
            "status": r.status,
            "generation": r.generation,
            "is_champion": r.is_champion,
            "strategy_config": r.strategy_config if r.is_champion else None,
            "total_return": r.total_return,
            "sharpe_ratio": r.sharpe_ratio,
            "max_drawdown": r.max_drawdown,
            "win_rate": r.win_rate,
            "profit_factor": r.profit_factor,
            "total_trades": r.total_trades,
            "final_equity": r.final_equity,
            "duration_seconds": r.duration_seconds,
            "bars_total": r.bars_total,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in runs
    ]


@router.get("/runs/{run_id}")
def get_run_detail(run_id: str, db: Session = Depends(get_db)):
    """获取回测详情+交易明细"""
    run = db.query(BacktestRun).filter(BacktestRun.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="回测记录不存在")

    trades = db.query(BacktestTrade).filter(
        BacktestTrade.run_id == run_id
    ).order_by(BacktestTrade.entry_bar.asc()).all()

    return {
        "run": {
            "run_id": run.run_id,
            "template_id": run.template_id,
            "strategy_name": run.strategy_name,
            "symbol": run.symbol,
            "timeframe": run.timeframe,
            "status": run.status,
            "generation": run.generation,
            "is_champion": run.is_champion,
            "strategy_config": run.strategy_config,
            "risk_params": run.risk_params,
            "total_return": run.total_return,
            "annualized_return": run.annualized_return,
            "sharpe_ratio": run.sharpe_ratio,
            "max_drawdown": run.max_drawdown,
            "win_rate": run.win_rate,
            "profit_factor": run.profit_factor,
            "total_trades": run.total_trades,
            "avg_trade_return": run.avg_trade_return,
            "max_consecutive_wins": run.max_consecutive_wins,
            "max_consecutive_losses": run.max_consecutive_losses,
            "avg_holding_bars": run.avg_holding_bars,
            "final_equity": run.final_equity,
            "duration_seconds": run.duration_seconds,
            "bars_total": run.bars_total,
            "equity_curve": run.equity_curve,
        },
        "trades": [
            {
                "side": t.side,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "pnl": t.pnl,
                "pnl_pct": t.pnl_pct,
                "exit_reason": t.exit_reason,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "entry_bar": t.entry_bar,
                "exit_bar": t.exit_bar,
                "quantity": t.quantity,
                "leverage": t.leverage,
                "fee": t.fee,
            }
            for t in trades
        ],
    }


@router.get("/champions")
def list_champions(db: Session = Depends(get_db)):
    """获取所有冠军策略"""
    runs = db.query(BacktestRun).filter(
        BacktestRun.is_champion == True
    ).order_by(BacktestRun.sharpe_ratio.desc()).all()
    return [
        {
            "run_id": r.run_id,
            "template_id": r.template_id,
            "strategy_name": r.strategy_name,
            "symbol": r.symbol,
            "timeframe": r.timeframe,
            "generation": r.generation,
            "is_champion": True,
            "strategy_config": r.strategy_config,
            "sharpe_ratio": r.sharpe_ratio,
            "win_rate": r.win_rate,
            "max_drawdown": r.max_drawdown,
            "total_return": r.total_return,
            "profit_factor": r.profit_factor,
            "total_trades": r.total_trades,
        }
        for r in runs
    ]
