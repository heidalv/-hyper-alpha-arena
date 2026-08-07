"""
Kelly 仓位管理 & 系统协调 API Routes

活跃端点：
  GET  /api/rl/kelly/portfolio                — 多币种组合 Kelly
  GET  /api/rl/coordinator/status             — 系统协调器状态
  POST /api/rl/coordinator/optimize           — 触发协调优化

已移除 (DRL 已于 2026-06-11 下线)：
  /status, /shadow-advice/{symbol}, /kelly/{symbol},
  /train, /train/{task_id}/status, /training-status, /drl/performance
"""

import logging
import threading
import uuid
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rl", tags=["DRL & Kelly"])


# ════════════════════════════════════════════════════════
#  模块级单例（懒加载）
# ════════════════════════════════════════════════════════

_kelly_sizer = None


def _get_kelly_sizer():
    global _kelly_sizer
    if _kelly_sizer is None:
        try:
            from backend.services.rl import KellyPositionSizer
            _kelly_sizer = KellyPositionSizer()
        except Exception as e:
            logger.warning(f"[RL Routes] Cannot load KellyPositionSizer: {e}")
    return _kelly_sizer


# ════════════════════════════════════════════════════════
#  Kelly 组合 / 协调器
# ════════════════════════════════════════════════════════

def _collect_active_symbols(db, limit: int = 12) -> List[str]:
    """从近期 StrategyTrade / MultiSymbolKelly 取活跃币种列表。"""
    from backend.database.models import StrategyTrade, MultiSymbolKelly
    try:
        rows = db.query(StrategyTrade.symbol).filter(
            StrategyTrade.closed_at.isnot(None)
        ).order_by(StrategyTrade.closed_at.desc()).limit(300).all()
        seen, symbols = set(), []
        for (s,) in rows:
            if s and s not in seen:
                seen.add(s)
                symbols.append(s)
                if len(symbols) >= limit:
                    break
        if symbols:
            return symbols
    except Exception as e:
        logger.debug(f"[RL] collect_active_symbols from trades failed: {e}")

    try:
        rows = db.query(MultiSymbolKelly.symbol).order_by(
            MultiSymbolKelly.timestamp.desc()
        ).limit(200).all()
        seen, symbols = set(), []
        for (s,) in rows:
            if s and s not in seen:
                seen.add(s)
                symbols.append(s)
                if len(symbols) >= limit:
                    break
        return symbols
    except Exception:
        return []


@router.get("/kelly/portfolio")
async def get_kelly_portfolio():
    """多币种组合 Kelly — v3 扩展。
    聚合当前活跃币种的独立 Kelly → calculate_portfolio_kelly → PortfolioRiskAggregator.aggregate
    返回 `allocations / total_risk / correlation_risk / forced_adjustments`。"""
    from backend.database.connection import SessionLocal
    from backend.database.models import Account, StrategyTrade

    sizer = _get_kelly_sizer()
    if sizer is None:
        return {
            "allocations": [],
            "total_risk": 0.0,
            "correlation_risk": 0.0,
            "forced_adjustments": [],
            "reason": "KellyPositionSizer unavailable",
        }

    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.is_active == "true").first()
        equity = float(account.current_cash) if account and account.current_cash else 10000.0

        symbols = _collect_active_symbols(db, limit=12)
        if not symbols:
            return {
                "allocations": [],
                "total_risk": 0.0,
                "correlation_risk": 0.0,
                "forced_adjustments": [],
                "reason": "no active symbols",
                "equity": equity,
            }

        from backend.services.rl.kelly_position_sizer import KellyPositionResult
        kelly_results: Dict[str, Any] = {}
        for symbol in symbols:
            try:
                trades = db.query(StrategyTrade).filter(
                    StrategyTrade.symbol == symbol,
                    StrategyTrade.pnl.isnot(None),
                    StrategyTrade.closed_at.isnot(None),
                ).order_by(StrategyTrade.closed_at.desc()).limit(100).all()

                trade_history = [
                    {"pnl": t.pnl, "pnl_pct": t.pnl_pct}
                    for t in trades
                ] if trades else None

                win_rate, avg_win, avg_loss = 0.5, 0.0, 0.0
                if trade_history and len(trade_history) >= 5:
                    wins = [t["pnl"] for t in trade_history if t["pnl"] > 0]
                    losses = [t["pnl"] for t in trade_history if t["pnl"] < 0]
                    win_rate = len(wins) / len(trade_history)
                    avg_win = sum(wins) / len(wins) if wins else 0.0
                    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.01

                res = sizer.calculate(
                    equity=equity,
                    trade_history=trade_history,
                    win_rate=win_rate,
                    avg_win=avg_win,
                    avg_loss=avg_loss,
                )
                kelly_results[symbol] = res
            except Exception as _e:
                logger.debug(f"[RL] kelly {symbol} failed: {_e}")

        if not kelly_results:
            return {
                "allocations": [],
                "total_risk": 0.0,
                "correlation_risk": 0.0,
                "forced_adjustments": [],
                "reason": "no kelly results",
                "equity": equity,
            }

        try:
            from backend.services.rl.portfolio_risk_aggregator import portfolio_risk_aggregator
            allocation = portfolio_risk_aggregator.aggregate(kelly_results, equity=equity)
        except Exception:
            from backend.services.rl.portfolio_risk_aggregator import PortfolioRiskAggregator
            allocation = PortfolioRiskAggregator().aggregate(kelly_results, equity=equity)

        return {
            "equity": equity,
            "allocations": [
                {
                    "symbol": a.symbol,
                    "kelly_fraction": a.kelly_fraction,
                    "adjusted_fraction": a.adjusted_fraction,
                    "position_size": a.position_size,
                    "portfolio_fraction": getattr(a, "portfolio_fraction", 0.0),
                    "risk_contribution": getattr(a, "risk_contribution", 0.0),
                    "forced_adjustment": getattr(a, "forced_adjustment", None),
                }
                for a in allocation.allocations
            ],
            "total_risk": allocation.total_risk,
            "correlation_risk": allocation.correlation_risk,
            "forced_adjustments": list(allocation.forced_adjustments or []),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"[RL] kelly/portfolio error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"kelly portfolio failed: {e}")
    finally:
        db.close()



@router.get("/coordinator/status")
async def get_coordinator_status():
    """系统协调器状态 — v3 扩展。"""
    from backend.database.connection import SessionLocal
    from backend.database.models import SystemCoordinatorState
    from backend.services.rl.system_coordinator import system_coordinator

    db = SessionLocal()
    try:
        state = db.query(SystemCoordinatorState).first()
        state_dict: Dict[str, Any] = {}
        if state:
            state_dict = {
                "last_evolution_at": state.last_evolution_at.isoformat() if state.last_evolution_at else None,
                "last_drl_training_at": state.last_drl_training_at.isoformat() if state.last_drl_training_at else None,
                "current_regime": state.current_regime,
                "regime_confidence": float(state.regime_confidence or 0.0),
                "auto_tuning_enabled": bool(state.auto_tuning_enabled),
                "sync_status": state.sync_status,
                "active_transaction_id": state.active_transaction_id,
                "last_kelly_update_at": state.last_kelly_update_at.isoformat() if state.last_kelly_update_at else None,
                "last_correlation_update_at": state.last_correlation_update_at.isoformat() if state.last_correlation_update_at else None,
                "updated_at": state.updated_at.isoformat() if state.updated_at else None,
            }

        coord_status = system_coordinator.get_status(db)
        return {
            "db_state": state_dict,
            "coordinator": coord_status,
            "tdi_injected": _is_tdi_injected(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"[RL] coordinator/status error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"coordinator status failed: {e}")
    finally:
        db.close()


def _is_tdi_injected() -> bool:
    try:
        from backend.services.trading_decision_interface import trading_decision_interface
        return getattr(trading_decision_interface, "_coordinator", None) is not None
    except Exception:
        return False


@router.get("/drl/performance")
async def get_drl_performance(days: int = 7):
    """DRL 绩效快照（DRL 下线后返回空结构，保持 API 契约）。"""
    days = max(1, min(90, int(days or 7)))
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import StrategyTrade

        db = SessionLocal()
        try:
            since = datetime.now(timezone.utc) - timedelta(days=days)
            trades = (
                db.query(StrategyTrade)
                .filter(StrategyTrade.closed_at.isnot(None), StrategyTrade.closed_at >= since)
                .limit(500)
                .all()
            )
            total = len(trades)
            wins = sum(1 for t in trades if (t.pnl or 0) > 0)
            accuracy = (wins / total) if total else 0.0
            avg_pnl = (sum(float(t.pnl or 0) for t in trades) / total) if total else 0.0
            per_symbol: Dict[str, Any] = {}
            for t in trades:
                sym = (t.symbol or "UNKNOWN").upper()
                bucket = per_symbol.setdefault(sym, {"count": 0, "wins": 0, "pnl": 0.0})
                bucket["count"] += 1
                if (t.pnl or 0) > 0:
                    bucket["wins"] += 1
                bucket["pnl"] += float(t.pnl or 0)
            return {
                "total_predictions": total,
                "correct_count": wins,
                "accuracy": round(accuracy, 4),
                "avg_pnl": round(avg_pnl, 4),
                "per_symbol": per_symbol,
                "daily_trend": [],
                "days": days,
            }
        finally:
            db.close()
    except Exception as e:
        logger.warning("[RL] drl/performance fallback: %s", e)
        return {
            "total_predictions": 0,
            "correct_count": 0,
            "accuracy": 0.0,
            "avg_pnl": 0.0,
            "per_symbol": {},
            "daily_trend": [],
            "days": days,
        }


class CoordinatorOptimizeRequest(BaseModel):
    reason: Optional[str] = "manual_trigger"


@router.post("/coordinator/optimize")
async def trigger_coordinator_optimize(req: Optional[CoordinatorOptimizeRequest] = None):
    """触发协调优化 — v3 扩展。调 unified_learning.trigger_coordinated_optimization。"""
    from backend.database.connection import SessionLocal
    from backend.services.unified_learning_service import unified_learning

    reason = (req.reason if req else "manual_trigger") or "manual_trigger"
    db = SessionLocal()
    try:
        unified_learning.trigger_coordinated_optimization(db, reason=reason)
        return {
            "triggered": True,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"[RL] coordinator/optimize error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"coordinator optimize failed: {e}")
    finally:
        db.close()
