"""
风控监控 API 路由 — Phase 4

前端 RiskPage 使用的 REST API：
  GET /api/risk/liquidation-risks     — 获取当前持仓爆仓风险
  GET /api/risk/alert-history         — 获取预警历史
  GET /api/risk/status                — 风控监控服务状态
"""

import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from backend.services.trading_pairs_config import get_user_trading_pairs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/risk", tags=["risk"])


class LockStrengthPatch(BaseModel):
    mode: str
    strength: int


@router.get("/lock-strength")
async def get_lock_strength():
    """获取模拟盘 / 实盘锁仓强度及解析后的有效阈值。"""
    try:
        from backend.services.lock_strength_service import get_lock_strength_service
        return get_lock_strength_service().get_state()
    except Exception as e:
        logger.error("[RiskRoutes] get_lock_strength error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/lock-strength")
async def patch_lock_strength(body: LockStrengthPatch):
    """调节单模式锁仓强度（0=关闭/最宽松 … 100=最严格）。"""
    try:
        from backend.services.lock_strength_service import get_lock_strength_service
        svc = get_lock_strength_service()
        result = svc.set_strength(body.mode, body.strength)
        if body.mode.strip().lower() == "paper" and body.strength < 15:
            try:
                from backend.database.connection import SessionLocal
                from backend.database.models import FullAutoSession
                from backend.services.full_auto_trading_service import full_auto_service
                db = SessionLocal()
                try:
                    sessions = db.query(FullAutoSession).filter(
                        FullAutoSession.trading_mode == "paper",
                        FullAutoSession.status.in_(["running", "defensive", "paused"]),
                    ).all()
                    for sess in sessions:
                        if full_auto_service._paper_auto_unlock_session(db, sess):
                            full_auto_service._safe_commit(db, "lock_strength_unlock", session=sess)
                finally:
                    db.close()
            except Exception as unlock_err:
                logger.debug("[RiskRoutes] paper unlock after strength patch: %s", unlock_err)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("[RiskRoutes] patch_lock_strength error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/liquidation-risks")
async def get_liquidation_risks():
    """获取当前所有持仓的爆仓风险（非 SAFE 级别）"""
    try:
        from backend.services.liquidation_monitor import liquidation_monitor
        risks = liquidation_monitor.get_position_risks()
        status = liquidation_monitor.get_status()

        # 同时返回账户风控摘要
        summaries = []
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import Account
            from backend.services.risk_control_service import get_risk_control_service

            db = SessionLocal()
            try:
                accounts = db.query(Account).filter(Account.is_active == "true").all()
                svc = get_risk_control_service()
                for acc in accounts:
                    try:
                        daily_trades = svc._count_daily_trades(db, acc.id)
                        consecutive_losses = svc._count_consecutive_losses(db, acc.id)
                        # 获取账户余额
                        total_equity = 0.0
                        margin_usage = 0.0
                        daily_loss = 0.0
                        try:
                            if getattr(acc, 'trading_mode', '') == 'paper' or not getattr(acc, 'hyperliquid_enabled', False):
                                # PAPER 账户：从 PaperBalance 表读取
                                from backend.database.models import PaperBalance
                                pb = db.query(PaperBalance).filter(PaperBalance.account_id == acc.id).first()
                                if pb:
                                    total_equity = float(pb.total_equity or 0)
                                    frozen = float(pb.frozen_margin or 0)
                                    margin_usage = (frozen / total_equity * 100) if total_equity > 0 else 0
                                    initial = float(pb.initial_balance or 0)
                                    if initial > 0:
                                        daily_loss = max(0, (initial - total_equity) / initial)
                            else:
                                # 实盘账户：从交易所 API 获取
                                from backend.services.hyperliquid_trading_client import HyperliquidTradingClient
                                _client = HyperliquidTradingClient(
                                    wallet_address=acc.wallet_address or "",
                                    private_key=acc.api_secret or "",
                                    is_mainnet=(getattr(acc, 'environment', '') == "mainnet"),
                                )
                                _acct_info = _client.get_account_state(db)
                                if _acct_info:
                                    total_equity = float(_acct_info.get("total_equity", 0) or 0)
                                    margin_usage = float(_acct_info.get("margin_usage_percent", 0) or 0)
                        except Exception:
                            pass  # API 不可用时保持 0
                        summaries.append({
                            "account_id": acc.id,
                            "total_equity": total_equity,
                            "daily_loss_ratio": daily_loss,
                            "margin_usage_percent": margin_usage,
                            "is_circuit_breaker_active": acc.id in svc._circuit_breaker_cache,
                            "daily_trades": daily_trades,
                            "consecutive_losses": consecutive_losses,
                            "max_single_symbol_ratio": svc.config.max_single_symbol_ratio,
                        })
                    except Exception as e:
                        logger.debug(f"[RiskRoutes] account {acc.id} summary error: {e}")
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[RiskRoutes] summaries error: {e}")

        return {
            "risks": [
                {
                    "account_id": r.account_id,
                    "symbol": r.symbol,
                    "side": r.side,
                    "entry_price": r.entry_price,
                    "mark_price": r.mark_price,
                    "liquidation_price": r.liquidation_price,
                    "distance_to_liq_pct": r.distance_to_liq_pct,
                    "risk_level": r.risk_level.value if hasattr(r.risk_level, 'value') else r.risk_level,
                    "leverage": r.leverage,
                    "position_value": r.position_value,
                    "unrealized_pnl": r.unrealized_pnl,
                }
                for r in risks
            ],
            "summaries": summaries,
            "monitor_status": status,
        }
    except Exception as e:
        logger.error(f"[RiskRoutes] get_liquidation_risks error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/alert-history")
async def get_alert_history(limit: int = Query(20, ge=1, le=100)):
    """获取最近的爆仓预警记录"""
    try:
        from backend.services.liquidation_monitor import liquidation_monitor
        alerts = liquidation_monitor.get_alert_history(limit=limit)
        # 计算今日预警次数
        from datetime import datetime, timezone
        _now = datetime.now(timezone.utc)
        _today_start = _now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_count = 0
        for a in alerts:
            _ts = a.get("timestamp")
            if _ts:
                try:
                    if isinstance(_ts, str):
                        _dt = datetime.fromisoformat(_ts.replace("Z", "+00:00"))
                    elif isinstance(_ts, datetime):
                        _dt = _ts if getattr(_ts, 'tzinfo', None) else _ts.replace(tzinfo=timezone.utc)
                    else:
                        continue
                    if _dt >= _today_start:
                        today_count += 1
                except Exception:
                    pass
        return {"alerts": alerts, "total": len(alerts), "today_count": today_count}
    except Exception as e:
        logger.error(f"[RiskRoutes] get_alert_history error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/profit-drawdown/status")
async def get_profit_drawdown_status():
    """获取所有持仓的盈利回撤保护状态 (D6)"""
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import PaperPosition
        from backend.services.profit_drawdown_guard import get_profit_drawdown_guard
        from backend.services.paper_trading_engine import paper_engine

        guard = get_profit_drawdown_guard()
        db = SessionLocal()
        try:
            positions = db.query(PaperPosition).filter(
                PaperPosition.status == "open"
            ).all()

            pos_status = []
            for pos in positions:
                pos_id = pos.id
                peak = paper_engine._peak_profit_cache.get(pos_id, 0)
                current_upnl = float(pos.unrealized_pnl or 0) + float(pos.partial_realized_pnl or 0)
                entry = float(pos.entry_price or 0)
                nature = getattr(pos, 'trade_nature', 'swing') or 'swing'

                # 获取该币种的阈值
                threshold_info = guard.get_threshold_info(pos.symbol, nature)

                # 计算回撤
                dd_ratio = 0.0
                if peak > 0:
                    dd_ratio = (peak - current_upnl) / peak
                    dd_ratio = max(0.0, dd_ratio)

                protection_active = dd_ratio >= threshold_info["effective_threshold"] and peak > entry * float(pos.size or 0) * 0.01

                pos_status.append({
                    "position_id": pos_id,
                    "symbol": pos.symbol,
                    "side": pos.side,
                    "nature": nature,
                    "entry_price": entry,
                    "current_price": float(pos.mark_price or 0),
                    "peak_profit": round(peak, 2),
                    "current_upnl": round(current_upnl, 2),
                    "drawdown_ratio": round(dd_ratio, 3),
                    "threshold": threshold_info["effective_threshold"],
                    "volatility_class": threshold_info["volatility_class"],
                    "protection_active": protection_active,
                    "protection_would_trigger": dd_ratio >= threshold_info["effective_threshold"],
                })

            return {
                "positions": pos_status,
                "total_positions": len(pos_status),
                "positions_in_drawdown": sum(1 for p in pos_status if p["protection_would_trigger"]),
            }
        finally:
            db.close()
    except Exception as e:
        logger.error(f"[RiskRoutes] profit-drawdown/status error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/profit-drawdown/thresholds")
async def get_profit_drawdown_thresholds():
    """获取各币种的盈利回撤保护阈值配置 (D6)"""
    try:
        from backend.services.profit_drawdown_guard import (
            get_profit_drawdown_guard,
            BASE_DRAWDOWN_THRESHOLD,
            NATURE_ADJUSTMENT,
            PROFIT_LOCK_BUFFER,
        )
        guard = get_profit_drawdown_guard()

        # 展示所有活跃币种的阈值
        symbols = get_user_trading_pairs()
        thresholds = []
        for sym in symbols:
            # 注意：中长线合并后新仓位不再使用 swing 这个 nature（统一走 trend_follow），
            # 这里保留 swing 仅用于历史持仓展示——数据库中既有 swing 仓位仍需按其阈值回放。
            for nature in ["scalp", "intraday", "swing", "position", "trend_follow"]:
                info = guard.get_threshold_info(sym, nature)
                thresholds.append(info)

        return {
            "thresholds": thresholds,
            "base_config": {
                "volatility_bases": BASE_DRAWDOWN_THRESHOLD,
                "nature_adjustments": NATURE_ADJUSTMENT,
                "profit_lock_buffers": PROFIT_LOCK_BUFFER,
            },
        }
    except Exception as e:
        logger.error(f"[RiskRoutes] profit-drawdown/thresholds error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/profit-drawdown/history")
async def get_profit_drawdown_history(symbol: Optional[str] = Query(None)):
    """获取盈利回撤保护动作历史 (D6)"""
    try:
        from backend.services.profit_drawdown_guard import get_profit_drawdown_guard
        guard = get_profit_drawdown_guard()
        history = guard.get_action_history(symbol)
        return {"history": history}
    except Exception as e:
        logger.error(f"[RiskRoutes] profit-drawdown/history error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_risk_monitor_status():
    """获取风控监控服务状态"""
    try:
        from backend.services.liquidation_monitor import liquidation_monitor
        from backend.services.risk_control_service import get_risk_control_service
        status = liquidation_monitor.get_status()
        svc = get_risk_control_service()
        return {
            "liquidation_monitor": status,
            "risk_config": {
                "max_daily_trades": svc.config.max_daily_trades,
                "max_position_per_trade_ratio": svc.config.max_position_per_trade_ratio,
                "max_leverage": svc.config.max_leverage,
                "consecutive_loss_pause_threshold": svc.config.consecutive_loss_pause_threshold,
                "consecutive_loss_reduce_threshold": svc.config.consecutive_loss_reduce_threshold,
                "daily_loss_limit_ratio": svc.config.daily_loss_limit_ratio,
                "max_single_symbol_ratio": svc.config.max_single_symbol_ratio,
            }
        }
    except Exception as e:
        logger.error(f"[RiskRoutes] get_risk_monitor_status error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ──────────── Per-Account Risk Control Config ────────────

_RISK_FIELDS = [
    "max_trade_amount", "daily_trade_count_limit", "max_concurrent_positions",
    "per_symbol_max_position", "global_stop_loss_pct",
    "enable_trade_amount_limit", "enable_trade_count_limit", "enable_concurrent_position_limit",
]


class RiskConfigUpdate(BaseModel):
    max_trade_amount: Optional[float] = None
    daily_trade_count_limit: Optional[int] = None
    max_concurrent_positions: Optional[int] = None
    per_symbol_max_position: Optional[int] = None
    global_stop_loss_pct: Optional[float] = None
    enable_trade_amount_limit: Optional[str] = None
    enable_trade_count_limit: Optional[str] = None
    enable_concurrent_position_limit: Optional[str] = None


@router.get("/{account_id}/config")
async def get_risk_config(account_id: int):
    """获取指定账户的风控配置"""
    from backend.database.connection import SessionLocal
    from backend.database.models import RiskControlConfig

    db = SessionLocal()
    try:
        config = db.query(RiskControlConfig).filter(
            RiskControlConfig.account_id == account_id
        ).first()
        if not config:
            raise HTTPException(status_code=404, detail="Risk config not found")
        return {
            "account_id": account_id,
            **{f: getattr(config, f, None) for f in _RISK_FIELDS},
        }
    finally:
        db.close()


@router.put("/{account_id}/config")
async def update_risk_config(account_id: int, body: RiskConfigUpdate):
    """更新指定账户的风控配置"""
    from backend.database.connection import SessionLocal
    from backend.database.models import RiskControlConfig

    db = SessionLocal()
    try:
        config = db.query(RiskControlConfig).filter(
            RiskControlConfig.account_id == account_id
        ).first()
        if not config:
            raise HTTPException(status_code=404, detail="Risk config not found")

        update_data = body.model_dump(exclude_none=True)
        for key, value in update_data.items():
            if hasattr(config, key):
                setattr(config, key, value)

        db.commit()
        db.refresh(config)
        logger.info(f"[RiskRoutes] Updated risk config for account {account_id}")
        return {
            "account_id": account_id,
            **{f: getattr(config, f, None) for f in _RISK_FIELDS},
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"[RiskRoutes] update_risk_config error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.post("/reset-loss-protection")
async def reset_loss_protection(account_id: Optional[int] = Query(None)):
    """立即解除连亏保护冻结（并重置心理状态）。"""
    from backend.database.connection import SessionLocal
    from backend.services.position_memory_manager import reset_loss_protection_state

    db = SessionLocal()
    try:
        n = reset_loss_protection_state(db, account_id)
        return {"ok": True, "reset_accounts": n, "account_id": account_id}
    finally:
        db.close()
