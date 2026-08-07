"""
Strategy Analytics API routes.
Provides multi-dimensional analysis of trading decisions and performance.
"""

from datetime import datetime, date, timezone
from decimal import Decimal
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, case, and_, or_
from sqlalchemy.orm import Session

from backend.database.connection import SessionLocal, AnalyticsSessionLocal
from backend.database.dialect import dialect
from backend.database.models import AIDecisionLog, Account, PromptTemplate
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============== Pydantic Models ==============

class MetricsResponse(BaseModel):
    total_pnl: float
    total_fee: float
    net_pnl: float
    trade_count: int
    win_count: int
    loss_count: int
    win_rate: float
    avg_win: Optional[float]
    avg_loss: Optional[float]
    profit_factor: Optional[float]


class DataCompleteness(BaseModel):
    total_decisions: int
    with_strategy: int
    with_signal: int
    with_pnl: int


class TriggerTypeBreakdown(BaseModel):
    count: int
    net_pnl: float


# ============== Helper Functions ==============

def calculate_metrics(records: List[Dict]) -> Dict[str, Any]:
    """Calculate standard metrics from a list of decision records."""
    if not records:
        return {
            "total_pnl": 0.0,
            "total_fee": 0.0,
            "net_pnl": 0.0,
            "trade_count": 0,
            "win_count": 0,
            "loss_count": 0,
            "win_rate": 0.0,
            "avg_win": None,
            "avg_loss": None,
            "profit_factor": None,
        }

    total_pnl = sum(r.get("pnl", 0) or 0 for r in records)
    total_fee = sum(r.get("fee", 0) or 0 for r in records)
    net_pnl = total_pnl - total_fee

    wins = [r for r in records if (r.get("pnl") or 0) > 0]
    losses = [r for r in records if (r.get("pnl") or 0) < 0]

    win_count = len(wins)
    loss_count = len(losses)
    trade_count = len(records)
    win_rate = win_count / trade_count if trade_count > 0 else 0.0

    total_win = sum(r.get("pnl", 0) or 0 for r in wins)
    total_loss = abs(sum(r.get("pnl", 0) or 0 for r in losses))

    avg_win = total_win / win_count if win_count > 0 else None
    avg_loss = -total_loss / loss_count if loss_count > 0 else None
    profit_factor = total_win / total_loss if total_loss > 0 else None

    return {
        "total_pnl": round(total_pnl, 2),
        "total_fee": round(total_fee, 2),
        "net_pnl": round(net_pnl, 2),
        "trade_count": trade_count,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": round(win_rate, 4),
        "avg_win": round(avg_win, 2) if avg_win else None,
        "avg_loss": round(avg_loss, 2) if avg_loss else None,
        "profit_factor": round(profit_factor, 2) if profit_factor else None,
    }


def get_trigger_type(decision: AIDecisionLog) -> str:
    """Determine trigger type for a decision."""
    if decision.signal_trigger_id is not None:
        return "signal"
    elif decision.executed == "true" and decision.operation in ("buy", "sell", "close"):
        return "scheduled"
    return "unknown"


def build_base_query(
    start_date: Optional[date],
    end_date: Optional[date],
    environment: Optional[str],
    account_id: Optional[int],
):
    """Build base query with common filters.

    Only includes decisions with non-zero realized_pnl (i.e., actually closed positions).
    This ensures statistics only count trades that have settled PnL,
    excluding opening trades (pnl=0) and unsync trades (pnl=NULL).
    """
    analytics_db = AnalyticsSessionLocal()
    try:
        query = analytics_db.query(AIDecisionLog).filter(
            AIDecisionLog.operation.in_(["buy", "sell", "close"]),
            AIDecisionLog.executed == "true",
            AIDecisionLog.realized_pnl.isnot(None),  # Exclude unsync trades
            AIDecisionLog.realized_pnl != 0,  # Exclude opening trades (no settled PnL)
        )

        if start_date:
            query = query.filter(AIDecisionLog.decision_time >= datetime.combine(start_date, datetime.min.time()))
        if end_date:
            query = query.filter(AIDecisionLog.decision_time <= datetime.combine(end_date, datetime.max.time()))
        if environment and environment != "all":
            query = query.filter(AIDecisionLog.hyperliquid_environment == environment)
        if account_id:
            query = query.filter(AIDecisionLog.account_id == account_id)

        return query.all()
    finally:
        analytics_db.close()


# ============== API Endpoints ==============


@router.get("/ai-decision-calibration")
def get_ai_decision_calibration(
    lookback_days: Optional[int] = Query(None, ge=1, le=365),
    force_refresh: bool = Query(False),
    backfill: bool = Query(False),
) -> Dict[str, Any]:
    """S2-8: LLM 置信度→实际胜率校准曲线（ai_decision_logs 拟合）。

    返回 PAVA 保序回归拟合的 conf→胜率曲线、样本量、分桶明细与质量信息，
    供决策链路视图（S2-11）与决策质量审计消费。

    `backfill=true` 时先执行样本回填：把已平仓 paper 仓位的盈亏回填到
    未结算的 buy/sell 决策日志（ai_decision_logs.realized_pnl），再重新拟合。
    """
    from backend.services.calibration.ai_decision_calibrator import (
        ai_decision_calibrator,
    )

    backfill_result = None
    if backfill:
        try:
            from backend.services.calibration.decision_pnl_backfill import (
                backfill_decision_pnl,
            )
            backfill_result = backfill_decision_pnl(
                lookback_days=lookback_days or 90)
            ai_decision_calibrator._model = None  # 回填后强制重新拟合
        except Exception as _bf_e:
            backfill_result = {"error": str(_bf_e)}

    if force_refresh:
        ai_decision_calibrator._model = None  # 强制重新拟合（重置 TTL 缓存）
    stats = ai_decision_calibrator.get_stats()

    # 冷启动/禁用时提示当前生效的回退来源
    eff_source = stats.get("source", "")
    enabled = True
    try:
        from backend.config import settings as _s
        enabled = bool(getattr(_s, "AI_DECISION_CALIBRATOR_ENABLED", True))
    except Exception:
        pass

    return {
        "calibration": stats,
        "enabled": enabled,
        "backfill": backfill_result,
        "lookback_days": lookback_days
        or ai_decision_calibrator._cfg("LOOKBACK_DAYS", 45),
        "estimate_sample": {
            "conf_0.3": ai_decision_calibrator.estimate_p_win(0.3).p_win,
            "conf_0.5": ai_decision_calibrator.estimate_p_win(0.5).p_win,
            "conf_0.7": ai_decision_calibrator.estimate_p_win(0.7).p_win,
            "conf_0.9": ai_decision_calibrator.estimate_p_win(0.9).p_win,
        },
        "note": (
            "cold_linear"
            if not stats.get("calibrated") and enabled
            else "calibrated" if stats.get("calibrated") else "disabled"
        ),
    }


@router.get("/summary")
def get_analytics_summary(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    environment: Optional[str] = Query("all"),
    account_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """Get overall analytics summary."""
    decisions = build_base_query( start_date, end_date, environment, account_id)


    # Convert to records for metrics calculation
    records = []
    signal_records = []
    scheduled_records = []
    unknown_records = []

    with_strategy = 0
    with_signal = 0
    with_pnl = 0

    for d in decisions:
        pnl = float(d.realized_pnl) if d.realized_pnl else 0
        fee = 0  # Fee is in HyperliquidTrade, not AIDecisionLog
        record = {"pnl": pnl, "fee": fee}
        records.append(record)

        trigger_type = get_trigger_type(d)
        if trigger_type == "signal":
            signal_records.append(record)
        elif trigger_type == "scheduled":
            scheduled_records.append(record)
        else:
            unknown_records.append(record)

        if d.prompt_template_id:
            with_strategy += 1
        if d.signal_trigger_id:
            with_signal += 1
        if d.realized_pnl:
            with_pnl += 1

    overview = calculate_metrics(records)

    return {
        "period": {
            "start": start_date.isoformat() if start_date else None,
            "end": end_date.isoformat() if end_date else None,
        },
        "overview": overview,
        "data_completeness": {
            "total_decisions": len(decisions),
            "with_strategy": with_strategy,
            "with_signal": with_signal,
            "with_pnl": with_pnl,
        },
        "by_trigger_type": {
            "signal": {
                "count": len(signal_records),
                "net_pnl": round(sum(r["pnl"] - r["fee"] for r in signal_records), 2),
            },
            "scheduled": {
                "count": len(scheduled_records),
                "net_pnl": round(sum(r["pnl"] - r["fee"] for r in scheduled_records), 2),
            },
            "unknown": {
                "count": len(unknown_records),
                "net_pnl": round(sum(r["pnl"] - r["fee"] for r in unknown_records), 2),
            },
        },
    }


@router.get("/by-strategy")
def get_analytics_by_strategy(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    environment: Optional[str] = Query("all"),
    account_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """Get analytics grouped by strategy (prompt template)."""
    decisions = build_base_query( start_date, end_date, environment, account_id)


    # Group by strategy
    by_strategy: Dict[Optional[int], List[Dict]] = {}
    strategy_names: Dict[int, str] = {}

    for d in decisions:
        strategy_id = d.prompt_template_id
        pnl = float(d.realized_pnl) if d.realized_pnl else 0
        record = {
            "pnl": pnl,
            "fee": 0,
            "trigger_type": get_trigger_type(d),
        }

        if strategy_id not in by_strategy:
            by_strategy[strategy_id] = []
        by_strategy[strategy_id].append(record)

    # Get strategy names
    strategy_ids = [sid for sid in by_strategy.keys() if sid is not None]
    if strategy_ids:
        templates = db.query(PromptTemplate).filter(PromptTemplate.id.in_(strategy_ids)).all()
        strategy_names = {t.id: t.name for t in templates}

    # Build response
    items = []
    for strategy_id, records in by_strategy.items():
        if strategy_id is None:
            continue

        signal_records = [r for r in records if r["trigger_type"] == "signal"]
        scheduled_records = [r for r in records if r["trigger_type"] == "scheduled"]

        items.append({
            "strategy_id": strategy_id,
            "strategy_name": strategy_names.get(strategy_id, f"Strategy {strategy_id}"),
            "metrics": calculate_metrics(records),
            "by_trigger_type": {
                "signal": {"count": len(signal_records), "net_pnl": round(sum(r["pnl"] for r in signal_records), 2)},
                "scheduled": {"count": len(scheduled_records), "net_pnl": round(sum(r["pnl"] for r in scheduled_records), 2)},
            },
        })

    # Sort by net_pnl descending
    items.sort(key=lambda x: x["metrics"]["net_pnl"], reverse=True)

    # Unattributed (no strategy)
    unattributed_records = by_strategy.get(None, [])

    return {
        "items": items,
        "unattributed": {
            "count": len(unattributed_records),
            "metrics": calculate_metrics(unattributed_records) if unattributed_records else None,
        },
    }


@router.get("/by-account")
def get_analytics_by_account(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    environment: Optional[str] = Query("all"),
    db: Session = Depends(get_db),
):
    """Get analytics grouped by account."""
    decisions = build_base_query( start_date, end_date, environment, None)


    # Group by account
    by_account: Dict[Optional[int], List[Dict]] = {}

    for d in decisions:
        account_id = d.account_id
        pnl = float(d.realized_pnl) if d.realized_pnl else 0
        record = {"pnl": pnl, "fee": 0, "trigger_type": get_trigger_type(d)}

        if account_id not in by_account:
            by_account[account_id] = []
        by_account[account_id].append(record)

    # Get account info (name, current model)
    account_ids = [aid for aid in by_account.keys() if aid is not None]
    account_info: Dict[int, Dict] = {}
    if account_ids:
        accounts = db.query(Account).filter(Account.id.in_(account_ids)).all()
        account_info = {
            a.id: {"name": a.name, "model": a.model, "environment": a.hyperliquid_environment}
            for a in accounts
        }

    # Build response
    items = []
    for account_id, records in by_account.items():
        if account_id is None:
            continue

        info = account_info.get(account_id, {})
        signal_records = [r for r in records if r["trigger_type"] == "signal"]
        scheduled_records = [r for r in records if r["trigger_type"] == "scheduled"]

        items.append({
            "account_id": account_id,
            "account_name": info.get("name", f"Account {account_id}"),
            "model": info.get("model"),
            "environment": info.get("environment"),
            "metrics": calculate_metrics(records),
            "by_trigger_type": {
                "signal": {"count": len(signal_records), "net_pnl": round(sum(r["pnl"] for r in signal_records), 2)},
                "scheduled": {"count": len(scheduled_records), "net_pnl": round(sum(r["pnl"] for r in scheduled_records), 2)},
            },
        })

    # Sort by net_pnl descending
    items.sort(key=lambda x: x["metrics"]["net_pnl"], reverse=True)

    # Unattributed (no account)
    unattributed_records = by_account.get(None, [])

    return {
        "items": items,
        "unattributed": {
            "count": len(unattributed_records),
            "metrics": calculate_metrics(unattributed_records) if unattributed_records else None,
        },
    }


@router.get("/by-symbol")
def get_analytics_by_symbol(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    environment: Optional[str] = Query("all"),
    account_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """Get analytics grouped by trading symbol."""
    decisions = build_base_query( start_date, end_date, environment, account_id)


    # Group by symbol
    by_symbol: Dict[Optional[str], List[Dict]] = {}

    for d in decisions:
        symbol = d.symbol
        pnl = float(d.realized_pnl) if d.realized_pnl else 0
        record = {"pnl": pnl, "fee": 0, "trigger_type": get_trigger_type(d)}

        if symbol not in by_symbol:
            by_symbol[symbol] = []
        by_symbol[symbol].append(record)

    # Build response
    items = []
    for symbol, records in by_symbol.items():
        if symbol is None:
            continue

        signal_records = [r for r in records if r["trigger_type"] == "signal"]
        scheduled_records = [r for r in records if r["trigger_type"] == "scheduled"]

        items.append({
            "symbol": symbol,
            "metrics": calculate_metrics(records),
            "by_trigger_type": {
                "signal": {"count": len(signal_records), "net_pnl": round(sum(r["pnl"] for r in signal_records), 2)},
                "scheduled": {"count": len(scheduled_records), "net_pnl": round(sum(r["pnl"] for r in scheduled_records), 2)},
            },
        })

    # Sort by net_pnl descending
    items.sort(key=lambda x: x["metrics"]["net_pnl"], reverse=True)

    # Unattributed (no symbol)
    unattributed_records = by_symbol.get(None, [])

    return {
        "items": items,
        "unattributed": {
            "count": len(unattributed_records),
            "metrics": calculate_metrics(unattributed_records) if unattributed_records else None,
        },
    }


@router.get("/by-operation")
def get_analytics_by_operation(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    environment: Optional[str] = Query("all"),
    account_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """Get analytics grouped by operation type (buy/sell/close)."""
    decisions = build_base_query( start_date, end_date, environment, account_id)


    # Group by operation
    by_operation: Dict[str, List[Dict]] = {}

    for d in decisions:
        operation = d.operation or "unknown"
        pnl = float(d.realized_pnl) if d.realized_pnl else 0
        record = {"pnl": pnl, "fee": 0, "trigger_type": get_trigger_type(d)}

        if operation not in by_operation:
            by_operation[operation] = []
        by_operation[operation].append(record)

    # Build response
    items = []
    for operation, records in by_operation.items():
        signal_records = [r for r in records if r["trigger_type"] == "signal"]
        scheduled_records = [r for r in records if r["trigger_type"] == "scheduled"]

        items.append({
            "operation": operation,
            "metrics": calculate_metrics(records),
            "by_trigger_type": {
                "signal": {"count": len(signal_records), "net_pnl": round(sum(r["pnl"] for r in signal_records), 2)},
                "scheduled": {"count": len(scheduled_records), "net_pnl": round(sum(r["pnl"] for r in scheduled_records), 2)},
            },
        })

    # Sort by trade_count descending
    items.sort(key=lambda x: x["metrics"]["trade_count"], reverse=True)

    return {"items": items}


@router.get("/by-trigger-type")
def get_analytics_by_trigger_type(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    environment: Optional[str] = Query("all"),
    account_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """Get analytics grouped by trigger type (signal/scheduled/unknown)."""
    decisions = build_base_query( start_date, end_date, environment, account_id)


    # Group by trigger type
    by_trigger: Dict[str, List[Dict]] = {"signal": [], "scheduled": [], "unknown": []}

    for d in decisions:
        trigger_type = get_trigger_type(d)
        pnl = float(d.realized_pnl) if d.realized_pnl else 0
        record = {"pnl": pnl, "fee": 0}
        by_trigger[trigger_type].append(record)

    # Build response
    items = []
    for trigger_type in ["signal", "scheduled", "unknown"]:
        records = by_trigger[trigger_type]
        if records:
            items.append({
                "trigger_type": trigger_type,
                "metrics": calculate_metrics(records),
            })

    # Sort by trade_count descending
    items.sort(key=lambda x: x["metrics"]["trade_count"], reverse=True)

    return {"items": items}


# ============== AI Attribution Analysis Routes ==============

from fastapi.responses import StreamingResponse
from pydantic import BaseModel as PydanticBaseModel
from backend.services.ai_attribution_service import (
    generate_attribution_analysis_stream,
    get_attribution_conversations,
    get_attribution_messages
)


class AiAttributionChatRequest(PydanticBaseModel):
    accountId: int
    userMessage: str
    conversationId: Optional[int] = None


@router.post("/ai-attribution/chat-stream")
async def ai_attribution_chat_stream(
    request: AiAttributionChatRequest,
    db: Session = Depends(get_db)
):
    """SSE streaming endpoint for AI attribution analysis chat."""
    return StreamingResponse(
        generate_attribution_analysis_stream(
            db=db,
            account_id=request.accountId,
            user_message=request.userMessage,
            conversation_id=request.conversationId
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.get("/ai-attribution/conversations")
async def list_attribution_conversations(db: Session = Depends(get_db)):
    """Get list of AI attribution analysis conversations."""
    conversations = get_attribution_conversations(db)
    return {"conversations": conversations}


@router.get("/ai-attribution/conversations/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: int,
    db: Session = Depends(get_db)
):
    """Get messages for a specific conversation."""
    messages = get_attribution_messages(db, conversation_id)
    return {"messages": messages}


# ============== Performance Dashboard Routes ==============

@router.get("/performance")
def get_performance_metrics(
    account_id: Optional[int] = Query(None),
    trading_mode: Optional[str] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """Get performance metrics for the dashboard."""
    environment = trading_mode if trading_mode in ["testnet", "mainnet"] else None
    decisions = build_base_query( start_date, end_date, environment, account_id)

    
    if not decisions:
        return {
            "status": "no_data",
            "message": "No trading decisions found for the specified criteria",
            "metrics": calculate_metrics([]),
        }
    
    records = []
    for d in decisions:
        pnl = float(d.realized_pnl) if d.realized_pnl else 0
        fee = float(d.fee) if d.fee else 0
        records.append({"pnl": pnl, "fee": fee})
    
    metrics = calculate_metrics(records)
    
    wins = [r for r in records if r["pnl"] > 0]
    losses = [r for r in records if r["pnl"] < 0]
    
    best_trade = max(wins, key=lambda x: x["pnl"], default={"pnl": 0})["pnl"] if wins else 0
    worst_trade = min(losses, key=lambda x: x["pnl"], default={"pnl": 0})["pnl"] if losses else 0
    
    total_pnl = metrics["total_pnl"]
    initial_capital = 10000.0
    total_pnl_pct = (total_pnl / initial_capital * 100) if initial_capital > 0 else 0
    
    return {
        "status": "success",
        "period": {
            "start": start_date.isoformat() if start_date else "all",
            "end": end_date.isoformat() if end_date else "now",
        },
        "metrics": metrics,
        "trades": {
            "total": len(records),
            "wins": len(wins),
            "losses": len(losses),
            "best_trade": best_trade,
            "worst_trade": worst_trade,
        },
    }


@router.get("/performance/summary")
def get_performance_summary(
    account_id: Optional[int] = Query(None),
    trading_mode: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Get performance summary for the dashboard."""
    environment = trading_mode if trading_mode in ["testnet", "mainnet"] else None
    decisions = build_base_query( None, None, environment, account_id)

    
    if not decisions:
        return {
            "status": "no_data",
            "period": {"start": "N/A", "end": "N/A"},
            "returns": {
                "total_pnl": 0,
                "total_pnl_pct": 0,
                "avg_trade_pnl": 0,
                "best_trade": 0,
                "worst_trade": 0,
            },
            "risk": {
                "max_drawdown_pct": 0,
                "current_drawdown": 0,
                "volatility": 0,
                "sharpe_ratio": 0,
                "sortino_ratio": 0,
                "var_95": 0,
            },
            "efficiency": {
                "win_rate": 0,
                "profit_factor": 0,
                "expectancy": 0,
                "avg_holding_hours": 0,
            },
            "consistency": {
                "consecutive_wins": 0,
                "consecutive_losses": 0,
                "trades_per_day": 0,
            },
        }
    
    records = []
    for d in decisions:
        pnl = float(d.realized_pnl) if d.realized_pnl else 0
        fee = float(d.fee) if d.fee else 0
        records.append({"pnl": pnl, "fee": fee})
    
    metrics = calculate_metrics(records)
    
    wins = [r for r in records if r["pnl"] > 0]
    losses = [r for r in records if r["pnl"] < 0]
    
    total_pnl = metrics["total_pnl"]
    initial_capital = 10000.0
    total_pnl_pct = (total_pnl / initial_capital * 100) if initial_capital > 0 else 0
    
    avg_trade_pnl = total_pnl / len(records) if records else 0
    best_trade = max(wins, key=lambda x: x["pnl"], default={"pnl": 0})["pnl"] if wins else 0
    worst_trade = min(losses, key=lambda x: x["pnl"], default={"pnl": 0})["pnl"] if losses else 0
    
    win_rate = metrics["win_rate"]
    profit_factor = metrics["profit_factor"] or 0
    
    expectancy = (win_rate * (metrics["avg_win"] or 0) - (1 - win_rate) * abs(metrics["avg_loss"] or 0)) if metrics["avg_win"] and metrics["avg_loss"] else 0
    
    return {
        "status": "success",
        "period": {"start": "all", "end": "now"},
        "returns": {
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 2),
            "avg_trade_pnl": round(avg_trade_pnl, 2),
            "best_trade": round(best_trade, 2),
            "worst_trade": round(worst_trade, 2),
        },
        "risk": {
            "max_drawdown_pct": 0,
            "current_drawdown": 0,
            "volatility": 0,
            "sharpe_ratio": 0,
            "sortino_ratio": 0,
            "var_95": 0,
        },
        "efficiency": {
            "win_rate": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "expectancy": round(expectancy, 2),
            "avg_holding_hours": 0,
        },
        "consistency": {
            "consecutive_wins": 0,
            "consecutive_losses": 0,
            "trades_per_day": 0,
        },
    }


@router.get("/strategy-performance-pivot")
def strategy_performance_pivot(
    account_id: Optional[int] = Query(None),
    days: int = Query(30, ge=1, le=365),
):
    """策略性能切面 — symbol × operation × decision_source

    返回 pivot table，每个组合包含:
      trades, win_rate, avg_pnl, sharpe, total_pnl, best_trade, worst_trade

    ai_decision_logs 在 Analytics DB，需用 AnalyticsSessionLocal 查询。
    """
    try:
        from sqlalchemy import text as _t

        analytics_db = AnalyticsSessionLocal()
        try:
            result = analytics_db.execute(
                _t("""
                    SELECT
                        COALESCE(symbol, '?') AS symbol,
                        COALESCE(operation, 'hold') AS operation,
                        COALESCE(decision_source, 'unknown') AS decision_source,
                        COUNT(*) AS trades,
                        ROUND(CAST(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS NUMERIC)
                              / GREATEST(COUNT(*), 1), 3) AS win_rate,
                        ROUND(CAST(AVG(COALESCE(realized_pnl, 0)) AS NUMERIC), 4) AS avg_pnl,
                        COALESCE(ROUND(CAST(
                            AVG(COALESCE(realized_pnl, 0))
                            / NULLIF(AVG(ABS(COALESCE(realized_pnl, 0))) * 2, 0) AS NUMERIC), 2
                        ), 0) AS sharpe_est,
                        ROUND(CAST(SUM(COALESCE(realized_pnl, 0)) AS NUMERIC), 4) AS total_pnl,
                        ROUND(CAST(MAX(COALESCE(realized_pnl, 0)) AS NUMERIC), 4) AS best_trade,
                        ROUND(CAST(MIN(COALESCE(realized_pnl, 0)) AS NUMERIC), 4) AS worst_trade
                    FROM ai_decision_logs
                    WHERE executed = 'true'
                      AND realized_pnl IS NOT NULL
                      AND decision_time >= """ + dialect.datetime_now_minus_param() + """
                      AND (CAST(:aid AS INTEGER) IS NULL OR account_id = CAST(:aid AS INTEGER))
                    GROUP BY symbol, operation, decision_source
                    ORDER BY total_pnl DESC
                    LIMIT 200
                """),
                {"days": days, "aid": account_id}
            )
            rows = result.fetchall()
        finally:
            analytics_db.close()

        pivot = []
        for row in rows:
            pivot.append({
                "symbol": row[0],
                "operation": row[1],
                "decision_source": row[2],
                "trades": int(row[3]),
                "win_rate": float(row[4]) if row[4] else 0,
                "avg_pnl": float(row[5]) if row[5] else 0,
                "sharpe_est": float(row[6]) if row[6] else 0,
                "total_pnl": float(row[7]) if row[7] else 0,
                "best_trade": float(row[8]) if row[8] else 0,
                "worst_trade": float(row[9]) if row[9] else 0,
            })

        return {
            "status": "ok",
            "period_days": days,
            "account_id": account_id,
            "pivot": pivot,
            "count": len(pivot),
        }
    except Exception as e:
        logger.error(f"[Analytics] strategy-performance-pivot error: {e}")
        return {"status": "error", "message": str(e)[:200]}


# ============== Trade Review Routes ==============

class TradeReviewRequest(BaseModel):
    trade_id: Optional[str] = None
    account_id: Optional[int] = None
    trading_mode: Optional[str] = None
    symbol: Optional[str] = None
    status: Optional[str] = None
    limit: Optional[int] = 30


@router.get("/reviews")
def get_trade_reviews(
    account_id: Optional[int] = Query(None),
    trading_mode: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(30, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Get reviewed trades with multi-dimensional scores."""
    try:
        environment = trading_mode if trading_mode in ["testnet", "mainnet"] else None
        # build_base_query 返回 list（已 .all()），在内存中过滤排序
        decisions = build_base_query(None, None, environment, account_id)
        if symbol:
            decisions = [d for d in decisions if d.symbol == symbol]
        decisions = sorted(
            decisions, key=lambda d: d.decision_time or datetime.min, reverse=True
        )[:limit]

        reviews = []
        total_pnl = 0.0
        score_dist = {"excellent": 0, "good": 0, "acceptable": 0, "poor": 0}
        dim_averages: Dict[str, float] = {}

        for d in decisions:
            pnl = float(d.realized_pnl or 0)
            pnl_pct = float(getattr(d, 'pnl_pct', 0) or 0)
            total_pnl += pnl

            # Compute basic review scores from available data
            entry_score = 5.0 + min(3.0, max(-3.0, pnl_pct * 50))
            risk_score = 5.0
            timing_score = 5.0 + min(3.0, max(-3.0, pnl * 20))
            regime_score = 5.0

            overall = round((entry_score * 0.3 + risk_score * 0.3 + timing_score * 0.2 + regime_score * 0.2), 1)

            if overall >= 8.0:
                score_dist["excellent"] += 1
            elif overall >= 6.0:
                score_dist["good"] += 1
            elif overall >= 4.0:
                score_dist["acceptable"] += 1
            else:
                score_dist["poor"] += 1

            review = {
                "trade_id": str(d.id),
                "symbol": d.symbol or "?",
                "side": d.operation or "buy",
                "entry_price": 0,
                "exit_price": 0,
                "quantity": float(getattr(d, 'target_portion', 0) or 0),
                "entry_time": d.decision_time.isoformat() if d.decision_time else None,
                "exit_time": None,
                "pnl": round(pnl, 4),
                "pnl_pct": round(pnl_pct, 4),
                "status": "completed" if d.realized_pnl is not None else "pending",
                "overall_score": overall,
                "max_score": 10.0,
                "dimensions": {
                    "entry_quality": {
                        "dimension": "entry_quality",
                        "score": round(entry_score, 1),
                        "weight": 0.30,
                        "weighted_score": round(entry_score * 0.30, 2),
                        "comments": [],
                        "issues": [],
                        "suggestions": [],
                    },
                    "risk_management": {
                        "dimension": "risk_management",
                        "score": round(risk_score, 1),
                        "weight": 0.30,
                        "weighted_score": round(risk_score * 0.30, 2),
                        "comments": [],
                        "issues": [],
                        "suggestions": [],
                    },
                    "timing": {
                        "dimension": "timing",
                        "score": round(timing_score, 1),
                        "weight": 0.20,
                        "weighted_score": round(timing_score * 0.20, 2),
                        "comments": [],
                        "issues": [],
                        "suggestions": [],
                    },
                    "market_regime": {
                        "dimension": "market_regime",
                        "score": round(regime_score, 1),
                        "weight": 0.20,
                        "weighted_score": round(regime_score * 0.20, 2),
                        "comments": [],
                        "issues": [],
                        "suggestions": [],
                    },
                },
                "conclusion": _review_conclusion(overall, pnl),
                "lessons_learned": _review_lessons(pnl, pnl_pct),
                "improvement_actions": _review_actions(overall),
                "market_regime_entry": getattr(d, 'market_regime', None),
                "ai_confidence": float(getattr(d, 'confidence', 0) or 0),
                "ai_reasoning": (d.reason or "")[:200] if d.reason else "",
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
            }
            reviews.append(review)

        # Compute summary
        avg_score = round(sum(r["overall_score"] for r in reviews) / max(len(reviews), 1), 1)
        wins = [r for r in reviews if r["pnl"] > 0]

        summary = {
            "total_reviews": len(reviews),
            "avg_overall_score": avg_score,
            "score_distribution": score_dist,
            "total_pnl": round(total_pnl, 2),
            "avg_pnl": round(total_pnl / max(len(reviews), 1), 2),
            "win_rate": round(len(wins) / max(len(reviews), 1), 3),
            "dimension_averages": dim_averages,
        }

        return {"reviews": reviews, "summary": summary}
    except Exception as e:
        logger.error(f"[Analytics] reviews error: {e}")
        return {"reviews": [], "summary": {"total_reviews": 0, "avg_overall_score": 0, "score_distribution": {}, "total_pnl": 0, "avg_pnl": 0, "win_rate": 0, "dimension_averages": {}}}


@router.get("/reviews/{trade_id}")
def get_trade_review_by_id(
    trade_id: str,
    db: Session = Depends(get_db),
):
    """Get a single trade review by trade ID."""
    try:
        analytics_db = AnalyticsSessionLocal()
        try:
            d = analytics_db.query(AIDecisionLog).filter(AIDecisionLog.id == int(trade_id)).first()
        finally:
            analytics_db.close()
        if not d:
            return {"error": "Trade not found"}

        pnl = float(d.realized_pnl or 0)
        pnl_pct = float(getattr(d, 'pnl_pct', 0) or 0)

        review = {
            "trade_id": str(d.id),
            "symbol": d.symbol or "?",
            "side": d.operation or "buy",
            "entry_price": 0,
            "exit_price": 0,
            "quantity": float(getattr(d, 'target_portion', 0) or 0),
            "entry_time": d.decision_time.isoformat() if d.decision_time else None,
            "exit_time": None,
            "pnl": round(pnl, 4),
            "pnl_pct": round(pnl_pct, 4),
            "status": "completed",
            "overall_score": round(5.0 + min(3.0, max(-3.0, pnl_pct * 50)), 1),
            "max_score": 10.0,
            "dimensions": {},
            "conclusion": _review_conclusion(5.0, pnl),
            "lessons_learned": _review_lessons(pnl, pnl_pct),
            "improvement_actions": [],
            "market_regime_entry": getattr(d, 'market_regime', None),
            "ai_confidence": float(getattr(d, 'confidence', 0) or 0),
            "ai_reasoning": (d.reason or "")[:200] if d.reason else "",
        }
        return review
    except ValueError:
        return {"error": "Invalid trade ID format"}
    except Exception as e:
        logger.error(f"[Analytics] review/{trade_id} error: {e}")
        return {"error": str(e)[:200]}


@router.post("/reviews/{trade_id}/trigger")
def trigger_trade_review(
    trade_id: str,
    db: Session = Depends(get_db),
):
    """Trigger a review for a specific trade."""
    review = get_trade_review_by_id(trade_id, db)
    if "error" in review:
        return {"success": False, "review": None, "error": review["error"]}
    return {"success": True, "review": review}


def _review_conclusion(score: float, pnl: float) -> str:
    if score >= 8.0:
        return "Excellent trade with strong execution across all dimensions."
    elif score >= 6.0:
        return "Good trade overall with some room for improvement."
    elif score >= 4.0:
        return "Acceptable but needs refinement in execution or timing."
    else:
        return "Poor trade — review strategy and risk parameters."


def _review_lessons(pnl: float, pnl_pct: float) -> list:
    lessons = []
    if pnl < 0:
        lessons.append(f"Loss of ${abs(pnl):.2f} ({abs(pnl_pct):.2f}%) — verify stop-loss placement")
        if abs(pnl_pct) > 0.05:
            lessons.append("Large loss detected — consider reducing position size")
    else:
        lessons.append(f"Profitable trade: +${pnl:.2f} (+{pnl_pct:.2f}%)")
    return lessons


def _review_actions(score: float) -> list:
    if score < 5.0:
        return ["Review entry criteria", "Tighten stop-loss", "Reduce position on next similar setup"]
    return []


# ============== Learning & Insights Routes ==============


@router.get("/learning/insights")
def get_learning_insights(
    account_id: Optional[int] = Query(None),
    trading_mode: Optional[str] = Query(None),
    insight_type: Optional[str] = Query(None),
    min_confidence: float = Query(0.3, ge=0.0, le=1.0),
    db: Session = Depends(get_db),
):
    """Get AI-generated learning insights from trading patterns."""
    try:
        environment = trading_mode if trading_mode in ["testnet", "mainnet"] else None
        decisions = build_base_query(None, None, environment, account_id)
        decisions = sorted(
            decisions, key=lambda d: d.decision_time or datetime.min, reverse=True
        )[:200]

        insights = []
        if not decisions:
            return {"insights": []}

        # Insight: Win rate by operation
        total = len(decisions)
        wins = sum(1 for d in decisions if (d.realized_pnl or 0) > 0)
        wr = wins / max(total, 1)

        if total >= 5:
            insights.append({
                "insight_type": "performance_pattern",
                "title": "Overall Win Rate Analysis",
                "description": f"Win rate is {wr:.1%} over the last {total} closed trades. "
                               f"{'Above' if wr >= 0.45 else 'Below'} the 45% target threshold.",
                "evidence": [f"{wins} wins out of {total} trades"],
                "recommendation": "Consider tightening entry criteria" if wr < 0.4 else "Continue current strategy",
                "confidence": min(0.9, 0.5 + total / 100),
                "supporting_trades": total,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "applicable": True,
            })

        # Insight: PnL distribution
        pnls = [float(d.realized_pnl or 0) for d in decisions]
        avg_pnl = sum(pnls) / max(len(pnls), 1)
        if abs(avg_pnl) > 0 and len(pnls) >= 5:
            insights.append({
                "insight_type": "pnl_pattern",
                "title": "PnL Distribution Analysis",
                "description": f"Average PnL per trade is ${avg_pnl:+.2f}. "
                               f"{'Positive' if avg_pnl > 0 else 'Negative'} expectancy detected.",
                "evidence": [f"Avg PnL: ${avg_pnl:+.2f}", f"Total trades: {len(pnls)}"],
                "recommendation": "Increase position on high-confidence setups" if avg_pnl > 0 else "Reduce risk exposure",
                "confidence": min(0.85, 0.4 + len(pnls) / 200),
                "supporting_trades": len(pnls),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "applicable": True,
            })

        # Filter by type if specified
        if insight_type:
            insights = [i for i in insights if i["insight_type"] == insight_type]

        # Filter by confidence
        insights = [i for i in insights if i["confidence"] >= min_confidence]

        return {"insights": insights}
    except Exception as e:
        logger.error(f"[Analytics] learning/insights error: {e}")
        return {"insights": []}


@router.get("/learning/recommendations")
def get_learning_recommendations(
    account_id: Optional[int] = Query(None),
    trading_mode: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Get actionable recommendations based on trading analysis."""
    try:
        environment = trading_mode if trading_mode in ["testnet", "mainnet"] else None
        decisions = build_base_query(None, None, environment, account_id)
        decisions = sorted(
            decisions, key=lambda d: d.decision_time or datetime.min, reverse=True
        )[:200]

        recommendations = []
        if not decisions:
            return {"recommendations": []}

        total = len(decisions)
        wins = sum(1 for d in decisions if (d.realized_pnl or 0) > 0)
        wr = wins / max(total, 1)
        pnls = [float(d.realized_pnl or 0) for d in decisions]
        total_pnl = sum(pnls)

        # Low win rate → adjust entry criteria
        if wr < 0.40 and total >= 10:
            recommendations.append({
                "category": "entry_quality",
                "priority": "high",
                "action": "Tighten entry confirmation requirements",
                "rationale": f"Win rate ({wr:.1%}) is below 40% target over {total} trades",
                "expected_impact": "Expected to improve win rate by 5-10%",
                "implementation": "Add EMA alignment check or increase minimum confidence threshold to 0.55",
            })

        # Negative total PnL → reduce risk
        if total_pnl < 0 and total >= 5:
            recommendations.append({
                "category": "risk_management",
                "priority": "high",
                "action": "Reduce position sizing by 30%",
                "rationale": f"Total PnL is negative (${total_pnl:.2f}) over {total} trades",
                "expected_impact": "Reduce drawdown exposure while strategy is refined",
                "implementation": "Set max_position_pct to 70% of current value",
            })

        # Low trade count → gather more data
        if total < 20:
            recommendations.append({
                "category": "data_collection",
                "priority": "medium",
                "action": "Continue paper trading to collect more data",
                "rationale": f"Only {total} trades available — insufficient for statistical significance",
                "expected_impact": "Achieve statistically significant sample (≥30 trades)",
                "implementation": "Run paper trading for at least 1 more week",
            })

        # Good win rate → scale up
        if wr >= 0.50 and total >= 15 and total_pnl > 0:
            recommendations.append({
                "category": "scaling",
                "priority": "medium",
                "action": "Consider increasing allocation to winning strategies",
                "rationale": f"Strong performance: {wr:.1%} WR, +${total_pnl:.2f} over {total} trades",
                "expected_impact": "Amplify returns on proven strategy",
                "implementation": "Increase tier budget allocation by 10-20%",
            })

        if priority:
            recommendations = [r for r in recommendations if r["priority"] == priority]

        return {"recommendations": recommendations}
    except Exception as e:
        logger.error(f"[Analytics] learning/recommendations error: {e}")
        return {"recommendations": []}


@router.get("/learning/report")
def get_learning_report(
    account_id: Optional[int] = Query(None),
    trading_mode: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Get a comprehensive learning report."""
    try:
        environment = trading_mode if trading_mode in ["testnet", "mainnet"] else None
        decisions = build_base_query(None, None, environment, account_id)
        decisions = sorted(
            decisions, key=lambda d: d.decision_time or datetime.min, reverse=True
        )[:500]

        insights_res = get_learning_insights(account_id=account_id, trading_mode=trading_mode, db=db)
        recs_res = get_learning_recommendations(account_id=account_id, trading_mode=trading_mode, db=db)

        # Factor performance summary
        pnls = [float(d.realized_pnl or 0) for d in decisions]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        # Regime performance from decision_context
        regime_stats: Dict[str, Dict] = {}
        for d in decisions:
            regime = getattr(d, 'market_regime', None) or "unknown"
            if regime not in regime_stats:
                regime_stats[regime] = {"trades": 0, "wins": 0, "pnl": 0.0}
            regime_stats[regime]["trades"] += 1
            pnl = float(d.realized_pnl or 0)
            if pnl > 0:
                regime_stats[regime]["wins"] += 1
            regime_stats[regime]["pnl"] += pnl

        regime_summary = {}
        for regime, stats in regime_stats.items():
            regime_summary[regime] = {
                "trades": stats["trades"],
                "win_rate": round(stats["wins"] / max(stats["trades"], 1), 3),
                "avg_pnl": round(stats["pnl"] / max(stats["trades"], 1), 4),
            }

        top_insights = [
            {"type": i["insight_type"], "title": i["title"], "confidence": i["confidence"],
             "supporting_trades": i["supporting_trades"], "recommendation": i["recommendation"]}
            for i in insights_res.get("insights", [])[:5]
        ]

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "insights_count": len(insights_res.get("insights", [])),
            "recommendations_count": len(recs_res.get("recommendations", [])),
            "top_insights": top_insights,
            "actionable_recommendations": recs_res.get("recommendations", []),
            "factor_performance_summary": {
                "pnl": {"sample_count": len(pnls), "avg_positive": round(sum(wins) / max(len(wins), 1), 4) if wins else 0,
                        "avg_negative": round(sum(losses) / max(len(losses), 1), 4) if losses else 0},
            },
            "regime_performance_summary": regime_summary,
        }
    except Exception as e:
        logger.error(f"[Analytics] learning/report error: {e}")
        return {"generated_at": datetime.now(timezone.utc).isoformat(), "insights_count": 0,
                "recommendations_count": 0, "top_insights": [], "actionable_recommendations": [],
                "factor_performance_summary": {}, "regime_performance_summary": {}}


@router.post("/learning/trigger")
def trigger_learning_analysis(
    account_id: Optional[int] = Query(None),
    trading_mode: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Trigger a learning analysis cycle."""
    try:
        insights_res = get_learning_insights(account_id=account_id, trading_mode=trading_mode, db=db)
        recs_res = get_learning_recommendations(account_id=account_id, trading_mode=trading_mode, db=db)
        return {
            "success": True,
            "insights_count": len(insights_res.get("insights", [])),
            "recommendations_count": len(recs_res.get("recommendations", [])),
        }
    except Exception as e:
        logger.error(f"[Analytics] learning/trigger error: {e}")
        return {"success": False, "insights_count": 0, "recommendations_count": 0}


@router.get("/strategy-runtime")
def get_strategy_runtime_report(
    window: str = Query("24h"),
    domain: str = Query("ai"),
    db: Session = Depends(get_db),
):
    """固定时间窗策略运行复盘（SRR）。"""
    try:
        from backend.services.strategy_runtime_report import get_or_build_runtime_report
        return get_or_build_runtime_report(db, window=window, domain=domain)
    except Exception as e:
        logger.error("[Analytics] strategy-runtime error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ============== Factor Analysis Routes ==============


@router.get("/factors/{symbol}/adaptive")
def get_adaptive_parameters(
    symbol: str,
    account_id: Optional[int] = Query(None),
    trading_mode: Optional[str] = Query(None),
):
    """Get adaptive parameters (factor weights + execution params) for a symbol."""
    try:
        # Get market regime
        regime = "ranging"
        regime_conf = 0.5
        try:
            from backend.services.market_regime_service import market_regime_service
            ctx = market_regime_service.get_regime_context(symbol)
            if ctx:
                regime = ctx.get("regime", "ranging")
                regime_conf = float(ctx.get("confidence", 0.5))
        except Exception:
            pass

        # Get factor weights
        factor_weights = {}
        try:
            from backend.services.factor_engine.factor_weighting import get_factor_weighting
            weighting = get_factor_weighting()
            aw = weighting.get_regime_weights(regime) if hasattr(weighting, 'get_regime_weights') else {}
            factor_weights = aw if aw else {
                "trend": 0.25, "momentum": 0.20, "volatility": 0.20,
                "volume": 0.15, "funding": 0.10, "liquidity": 0.10,
            }
        except Exception:
            factor_weights = {
                "trend": 0.25, "momentum": 0.20, "volatility": 0.20,
                "volume": 0.15, "funding": 0.10, "liquidity": 0.10,
            }

        return {
            "market_regime": regime,
            "regime_confidence": round(regime_conf, 3),
            "factor_weights": factor_weights,
            "factor_summary": f"Adaptive weights for {regime} regime (conf={regime_conf:.0%})",
            "execution_parameters": {
                "position_size_pct": 0.05,
                "stop_loss_pct": 0.03,
                "take_profit_pct": 0.08,
                "trailing_stop": True,
                "time_stop": False,
                "leverage": 1.0,
                "risk_reward_ratio": 2.67,
            },
            "execution_summary": "Default adaptive execution parameters",
        }
    except Exception as e:
        logger.error(f"[Analytics] factors/{symbol}/adaptive error: {e}")
        return {
            "market_regime": "unknown", "regime_confidence": 0, "factor_weights": {},
            "factor_summary": f"Error: {str(e)[:100]}", "execution_parameters": {}, "execution_summary": "",
        }


@router.get("/factors/{symbol}", deprecated=True)
def get_factor_values(
    symbol: str,
    account_id: Optional[int] = Query(None),
    trading_mode: Optional[str] = Query(None),
):
    """[DEPRECATED] 请改用 /api/factors/values/{symbol}。

    为消除因子计算双轨（旧 factor_engine vs 新 Registry），本端点已改为委托给
    统一 FactorService（Registry 路径），保证与 /api/factors/values 口径完全一致。
    保留仅为向后兼容，后续版本将移除。
    """
    try:
        from backend.services.factor_engine.factor_service import factor_service
        return factor_service.compute_as_list(symbol, timeframe="15m")
    except Exception as e:
        logger.error(f"[Analytics] factors/{symbol} error: {e}")
        return {"factors": [], "error": str(e)[:200]}


@router.get("/factors/adaptive")
def get_all_adaptive_parameters(
    account_id: Optional[int] = Query(None),
    trading_mode: Optional[str] = Query(None),
    symbols: Optional[str] = Query(None),
):
    """Get adaptive parameters for all tracked symbols."""
    try:
        sym_list = symbols.split(",") if symbols else ["BTC", "ETH"]
        result = {}
        for sym in sym_list:
            result[sym.strip()] = get_adaptive_parameters(sym.strip(), account_id, trading_mode)
        return result
    except Exception as e:
        logger.error(f"[Analytics] factors/adaptive error: {e}")
        return {}


# ============== SL/TP Calculator Route ==============

class SLTPRequest(BaseModel):
    entry_price: float
    side: str  # "buy" or "sell"
    atr: float
    strategy: Optional[dict] = None


@router.post("/sltp/{symbol}")
def calculate_sltp(
    symbol: str,
    request: SLTPRequest,
    account_id: Optional[int] = Query(None),
    trading_mode: Optional[str] = Query(None),
):
    """Calculate Stop-Loss and Take-Profit levels for a trade."""
    try:
        entry = request.entry_price
        side = request.side.lower()
        atr = request.atr or entry * 0.02
        is_long = side in ("buy", "long")

        sl_distance = atr * 1.5
        sl_price = round(entry - sl_distance, 4) if is_long else round(entry + sl_distance, 4)

        tp1_price = round(entry + sl_distance * 1.5, 4) if is_long else round(entry - sl_distance * 1.5, 4)
        tp2_price = round(entry + sl_distance * 2.5, 4) if is_long else round(entry - sl_distance * 2.5, 4)
        tp3_price = round(entry + sl_distance * 4.0, 4) if is_long else round(entry - sl_distance * 4.0, 4)

        return {
            "initial_stop": {
                "price": sl_price,
                "distance_pct": round(abs(entry - sl_price) / entry * 100, 2),
                "reason": f"1.5x ATR ({atr:.4f}) stop from entry",
            },
            "trailing_stop": {
                "price": round(entry - sl_distance * 0.5, 4) if is_long else round(entry + sl_distance * 0.5, 4),
                "type": "atr_trailing",
            },
            "breakeven_stop": {
                "price": round(entry + sl_distance * 0.3, 4) if is_long else round(entry - sl_distance * 0.3, 4),
            },
            "final_stop": sl_price,
            "take_profit_levels": {
                "tp1": {"price": tp1_price, "close_pct": 0.40},
                "tp2": {"price": tp2_price, "close_pct": 0.35},
                "tp3": {"price": tp3_price, "close_pct": 0.25},
            },
            "risk_reward_ratio": {
                "tp1_rr": round(1.5, 2),
                "tp2_rr": round(2.5, 2),
                "tp3_rr": round(4.0, 2),
            },
        }
    except Exception as e:
        logger.error(f"[Analytics] sltp/{symbol} error: {e}")
        return {"error": str(e)[:200]}


# ============== Position Size Calculator Route ==============

class PositionSizeRequest(BaseModel):
    entry_price: float
    stop_loss: float
    side: str
    win_rate: Optional[float] = None
    avg_win: Optional[float] = None
    avg_loss: Optional[float] = None
    volatility: Optional[float] = None
    confidence: Optional[float] = None


@router.post("/position-size/{symbol}")
def calculate_position_size(
    symbol: str,
    request: PositionSizeRequest,
    account_id: Optional[int] = Query(None),
    trading_mode: Optional[str] = Query(None),
):
    """Calculate optimal position size based on risk parameters."""
    try:
        entry = request.entry_price
        stop = request.stop_loss
        risk_pct = abs(entry - stop) / entry

        # Kelly-based calculation
        wr = request.win_rate or 0.45
        avg_win = request.avg_win or 0.03
        avg_loss = request.avg_loss or abs(risk_pct)
        rr = avg_win / max(avg_loss, 0.001)

        kelly = (wr * rr - (1 - wr)) / max(rr, 0.001)
        kelly = max(0, min(0.25, kelly))
        half_kelly = kelly * 0.5

        # Risk-based sizing
        risk_amount = 100  # default $100 risk per trade
        size_pct = risk_amount / (entry * risk_pct * 100) if risk_pct > 0 else 0.05

        conf = request.confidence or 0.6
        conf_adjustment = 0.5 + conf * 0.5
        final_size_pct = round(half_kelly * conf_adjustment, 4)

        return {
            "size": round(final_size_pct * 10000, 2),
            "size_pct": round(final_size_pct, 4),
            "risk_amount": round(risk_amount, 2),
            "risk_pct": round(risk_pct * 100, 2),
            "leverage": 1.0,
            "kelly_pct": round(kelly, 4),
            "adjustment_reasons": [
                f"Kelly fraction: {kelly:.2%} (half: {half_kelly:.2%})",
                f"Confidence adjustment: {conf:.0%} → {conf_adjustment:.0%} multiplier",
            ],
            "warnings": ["Position sizing is for reference only — verify with live market conditions"],
        }
    except Exception as e:
        logger.error(f"[Analytics] position-size/{symbol} error: {e}")
        return {"error": str(e)[:200]}


# ============== Report Generation Routes ==============

class ReportConfigRequest(BaseModel):
    title: str = "Trading Analysis Report"
    period_days: int = 30
    include_charts: bool = False
    include_details: bool = True
    format: str = "markdown"
    account_id: Optional[int] = None
    trading_mode: Optional[str] = None


def _generate_report_markdown(title: str, metrics: dict, extra_sections: list = None) -> str:
    """Generate a markdown report from metrics."""
    lines = [
        f"# {title}",
        f"\n**Generated**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "",
        "## Performance Summary",
        f"- Total Trades: {metrics.get('trade_count', 0)}",
        f"- Win Rate: {metrics.get('win_rate', 0):.1%}",
        f"- Net PnL: ${metrics.get('net_pnl', 0):,.2f}",
        f"- Profit Factor: {metrics.get('profit_factor', 0) or 0:.2f}",
        f"- Avg Win: ${metrics.get('avg_win', 0) or 0:,.2f}",
        f"- Avg Loss: ${metrics.get('avg_loss', 0) or 0:,.2f}",
        "",
    ]
    if extra_sections:
        for section in extra_sections:
            if isinstance(section, str):
                lines.append(section)
                lines.append("")
    return "\n".join(lines)


@router.post("/reports/performance")
def generate_performance_report(
    config: ReportConfigRequest,
    db: Session = Depends(get_db),
):
    """Generate a formatted performance report."""
    try:
        decisions = build_base_query(
            None, None,
            config.trading_mode if config.trading_mode in ["testnet", "mainnet"] else None,
            config.account_id,
        )
        decisions = sorted(
            decisions, key=lambda d: d.decision_time or datetime.min, reverse=True
        )[:500]
        records = [{"pnl": float(d.realized_pnl or 0), "fee": 0} for d in decisions]
        metrics = calculate_metrics(records)
        report = _generate_report_markdown(config.title, metrics)
        return report
    except Exception as e:
        logger.error(f"[Analytics] reports/performance error: {e}")
        return f"Error generating report: {str(e)[:200]}"


@router.post("/reports/review")
def generate_review_report(
    config: ReportConfigRequest,
    db: Session = Depends(get_db),
):
    """Generate a review-focused report."""
    try:
        reviews_res = get_trade_reviews(account_id=config.account_id, trading_mode=config.trading_mode,
                                        limit=50, db=db)
        summary = reviews_res.get("summary", {})
        lines = [
            f"# {config.title}",
            f"\n**Generated**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "",
            "## Review Summary",
            f"- Reviews: {summary.get('total_reviews', 0)}",
            f"- Avg Score: {summary.get('avg_overall_score', 0)}/10",
            f"- Win Rate: {summary.get('win_rate', 0):.1%}",
            f"- Total PnL: ${summary.get('total_pnl', 0):,.2f}",
            "",
            "## Score Distribution",
        ]
        dist = summary.get("score_distribution", {})
        for level, count in dist.items():
            lines.append(f"- {level}: {count}")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"[Analytics] reports/review error: {e}")
        return f"Error generating report: {str(e)[:200]}"


@router.post("/reports/learning")
def generate_learning_report_endpoint(
    config: ReportConfigRequest,
    db: Session = Depends(get_db),
):
    """Generate a learning insights report."""
    try:
        report_data = get_learning_report(account_id=config.account_id, trading_mode=config.trading_mode, db=db)
        lines = [
            f"# {config.title}",
            f"\n**Generated**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "",
            f"## Insights ({report_data.get('insights_count', 0)} found)",
        ]
        for insight in report_data.get("top_insights", []):
            lines.append(f"- **{insight.get('title', '')}**: {insight.get('recommendation', '')} (conf: {insight.get('confidence', 0):.0%})")
        lines.append("")
        lines.append(f"## Recommendations ({report_data.get('recommendations_count', 0)} found)")
        for rec in report_data.get("actionable_recommendations", []):
            lines.append(f"- [{rec.get('priority', '')}] {rec.get('action', '')}: {rec.get('rationale', '')}")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"[Analytics] reports/learning error: {e}")
        return f"Error generating report: {str(e)[:200]}"


@router.post("/reports/comprehensive")
def generate_comprehensive_report(
    config: ReportConfigRequest,
    db: Session = Depends(get_db),
):
    """Generate a comprehensive report combining performance, reviews, and learning."""
    try:
        perf_report = generate_performance_report(config, db)
        review_report = generate_review_report(config, db)
        learning_report = generate_learning_report_endpoint(config, db)
        combined = f"{perf_report}\n\n---\n\n{review_report}\n\n---\n\n{learning_report}"
        return combined
    except Exception as e:
        logger.error(f"[Analytics] reports/comprehensive error: {e}")
        return f"Error generating report: {str(e)[:200]}"


@router.get("/net-performance")
def get_net_performance(
    days: int = Query(7, ge=1, le=90, description="Lookback window in days"),
    db: Session = Depends(get_db),
):
    """V5 净值扣费看板：Net Profit Factor、fee/gross 比、盈亏比（净扣费口径）。

    按 close_reason / trade_nature / symbol 三个维度归因，
    数据来源 paper_orders（已平仓单），pnl 扣除手续费后统计。
    同时返回当前生效的 V5 运行时门槛（反馈闭环写入）。
    """
    try:
        from backend.services.decision_feedback_service import decision_feedback_service
        attribution = decision_feedback_service.build_net_attribution(db, days=days)

        # 当前生效的 V5 运行时门槛
        runtime_gates = {}
        try:
            import json as _json
            import os as _os
            gates_file = _os.path.join("data", "v5_runtime_gates.json")
            if _os.path.exists(gates_file):
                with open(gates_file, "r", encoding="utf-8") as f:
                    runtime_gates = _json.load(f)
        except Exception:
            pass

        summary = attribution.get("summary") or {}
        return {
            "days": days,
            "summary": summary,
            "headline": {
                "net_profit_factor": summary.get("net_profit_factor"),
                "fee_gross_ratio": summary.get("fee_gross_ratio"),
                "payoff_ratio": (
                    round(summary["avg_win"] / summary["avg_loss"], 2)
                    if summary.get("avg_win") and summary.get("avg_loss") else None
                ),
                "net_pnl": summary.get("net_pnl"),
                "fees": summary.get("fees"),
                "trades": summary.get("trades"),
                "win_rate": summary.get("win_rate"),
            },
            "by_close_reason": attribution.get("by_close_reason"),
            "by_nature": attribution.get("by_nature"),
            "by_symbol": attribution.get("by_symbol"),
            "v5_runtime_gates": runtime_gates,
        }
    except Exception as e:
        logger.error(f"[Analytics] net-performance error: {e}")
        return {"error": str(e), "days": days}


@router.get("/ab-comparison")
def get_ab_comparison(days: int = Query(30, description="Lookback window in days")):
    """
    A/B comparison of decision sources: win rate, avg PnL, block rate per source.

    Reads decision_arbiter.jsonl to compute source-level performance metrics.
    Useful for tuning which decision layers to trust.
    """
    try:
        from backend.analytics.ab_analyzer import ab_analyzer
        stats = ab_analyzer.compute(days=max(1, min(days, 365)))
        return {"days": days, "sources": stats}
    except Exception as e:
        logger.error(f"[Analytics] ab-comparison error: {e}")
        return {"error": str(e), "days": days, "sources": []}


@router.get("/by-agent")
def get_by_agent(
    days: int = Query(30, ge=1, le=365, description="Lookback window in days"),
    nature: Optional[str] = Query(
        None, description="Filter: swing or trend_follow (omit for both)",
    ),
    db: Session = Depends(get_db),
):
    """Swing / Trend Agent 维度绩效：净扣费口径 + 平均持仓 + scenario 命中率。

    注：中长线合并后 swing 不再是独立 tier——mid 已并入 long。这里接受 swing
    入参(向后兼容历史调用)，但筛选 swing 时一并查询 trend_follow(同一 tier)。
    """
    if nature and nature not in ("swing", "trend_follow"):
        raise HTTPException(
            status_code=400,
            detail="nature must be swing or trend_follow",
        )
    # mid 已并入 long：swing 与 trend_follow 视为同一 tier，统一走 trend_follow 口径
    nature = "trend_follow" if nature == "swing" else nature
    try:
        from backend.services.agent_analytics_service import build_by_agent_report
        return build_by_agent_report(db, days=days, nature=nature)
    except Exception as e:
        logger.error("[Analytics] by-agent error: %s", e)
        raise HTTPException(status_code=500, detail=str(e)[:200])
