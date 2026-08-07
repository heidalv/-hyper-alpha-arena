"""
Arbitrage API Routes — 套利系统独立 API

包含：
- 原始套利引擎端点（从 exchange_routes.py 迁移）
- 跨交易所套利端点（从 exchange_routes.py 迁移）
- V3 新增端点（协调器状态、资金池、监控指标、绩效、模式切换）
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.core.permissions import require_feature

logger = logging.getLogger(__name__)

# [阶段4 Task 4.4] 整组挂 require_feature("arbitrage"):arbitrage 属于
# RESTRICTED_FEATURES,仅 vip / admin 可用;free/pro → 403(三维权限-功能门控)。
router = APIRouter(
    tags=["Arbitrage"],
    dependencies=[Depends(require_feature("arbitrage"))],
)


def _get_manager():
    """获取 ExchangeManager 单例"""
    from backend.services.exchange.exchange_manager import get_exchange_manager
    return get_exchange_manager()


def _get_orchestrator():
    """获取 ArbitrageOrchestrator 单例"""
    try:
        from backend.services.arbitrage.orchestrator import arbitrage_orchestrator
        return arbitrage_orchestrator
    except Exception:
        return None


# ════════════════════════════════════════════════════════
#  套利引擎状态端点（迁移自 exchange_routes.py）
# ════════════════════════════════════════════════════════

@router.get("/status")
async def get_arbitrage_status():
    """套利引擎整体状态（含 V3 协调器状态）"""
    result = {
        "engine_enabled": True,
        "mode": "paper",
        "tick_count": 0,
        "active_positions": 0,
    }
    try:
        from backend.services.arbitrage.opportunity_scanner import opportunity_scanner
        from backend.services.arbitrage.emergency_handler import emergency_handler

        result["scanner_scan_count"] = opportunity_scanner.scan_count
        result["cached_opportunities"] = len(opportunity_scanner.get_active_opportunities())
        result["circuit_breaker_active"] = emergency_handler.is_circuit_breaker_active()
    except Exception as e:
        result["engine_enabled"] = False
        result["error"] = str(e)

    # V3 协调器状态
    orch = _get_orchestrator()
    if orch:
        try:
            result["orchestrator"] = orch.get_status()
            result["mode"] = orch.mode.value
            result["tick_count"] = orch.tick_count
            result["active_positions"] = len(orch.active_positions)
        except Exception:
            pass

    return result


@router.get("/positions")
async def get_arbitrage_positions(status: str = "active"):
    """获取套利仓位列表（从 ArbitragePosition DB 读取）"""
    from backend.database.connection import SessionLocal

    db = SessionLocal()
    try:
        from backend.database.models import ArbitragePosition
        query = db.query(ArbitragePosition)
        if status:
            query = query.filter(ArbitragePosition.status == status)
        positions = query.order_by(ArbitragePosition.entry_time.desc()).limit(50).all()

        return {
            "count": len(positions),
            "positions": [
                {
                    "position_id": p.position_id,
                    "symbol": p.symbol,
                    "strategy": p.strategy,
                    "long_size": float(p.long_size or 0),
                    "short_size": float(p.short_size or 0),
                    "delta": float(p.delta or 0),
                    "accumulated_funding": float(p.accumulated_funding or 0),
                    "status": p.status,
                    "entry_time": str(p.entry_time) if p.entry_time else None,
                    "close_time": str(p.close_time) if p.close_time else None,
                    "close_reason": p.close_reason,
                    "exchange_long": getattr(p, "exchange_long", None),
                    "exchange_short": getattr(p, "exchange_short", None),
                    "mode": getattr(p, "mode", "paper"),
                    "size_usd": float(getattr(p, "size_usd", 0) or 0),
                    "pnl": float(getattr(p, "pnl", 0) or 0),
                }
                for p in positions
            ],
        }
    except Exception as e:
        return {"count": 0, "positions": [], "error": str(e)}
    finally:
        db.close()


@router.get("/unified-positions")
async def get_unified_arbitrage_positions(status: str = "all"):
    """计划指定路径：聚合 Rebate/V3/Paper 合约腿。"""
    try:
        from backend.api.rebate_routes import get_unified_positions
        return await get_unified_positions(status=status)
    except Exception as e:
        return {"count": 0, "positions": [], "error": str(e)}


@router.get("/opportunities")
async def get_arbitrage_opportunities(symbol: str = None):
    """获取当前扫描到的套利机会"""
    try:
        from backend.services.arbitrage.opportunity_scanner import opportunity_scanner

        opps = opportunity_scanner.get_active_opportunities()
        if symbol:
            opps = [o for o in opps if o.symbol.upper() == symbol.upper()]

        return {
            "count": len(opps),
            "opportunities": [
                {
                    "opportunity_id": o.opportunity_id,
                    "symbol": o.symbol,
                    "strategy": o.strategy,
                    "expected_annual_yield": round(o.expected_annual_yield, 4),
                    "risk_score": round(o.risk_score, 4),
                    "confidence": round(o.confidence, 4),
                    "current_rate": o.funding_snapshot.current_rate if o.funding_snapshot else 0,
                    "rate_24h_avg": o.funding_snapshot.rate_24h_avg if o.funding_snapshot else 0,
                }
                for o in opps
            ],
        }
    except Exception as e:
        return {"count": 0, "opportunities": [], "error": str(e)}


@router.get("/fee-schedules")
async def get_fee_schedules():
    """获取各交易所费率配置"""
    try:
        from backend.services.arbitrage.fee_schedule import fee_registry

        return {
            "exchanges": {
                ex_id: {
                    "maker_rate": sched.maker_rate,
                    "taker_rate": sched.taker_rate,
                    "withdrawal_fee_usd": sched.withdrawal_fee_usd,
                    "slippage_bps": sched.slippage_bps_estimate,
                }
                for ex_id, sched in {
                    ex: fee_registry.get(ex) for ex in fee_registry.list_exchanges()
                }.items()
            }
        }
    except Exception as e:
        return {"exchanges": {}, "error": str(e)}


# ════════════════════════════════════════════════════════
#  跨交易所套利端点（迁移自 exchange_routes.py）
# ════════════════════════════════════════════════════════

@router.get("/cross-arb/spreads")
async def scan_cross_exchange_spreads(
    symbols: str = Query(default="BTC/USDT:USDT,ETH/USDT:USDT"),
):
    """扫描跨交易所价差。"""
    mgr = _get_manager()
    clients = mgr.get_all_clients()

    if len(clients) < 2:
        return {"message": "Need at least 2 exchanges configured", "spreads": []}

    sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
    spreads = []

    exchange_prices: Dict[str, Dict[str, float]] = {}
    tasks = []
    for key, client in clients.items():
        exchange = key.split(":")[0]
        for sym in sym_list:
            tasks.append((exchange, sym, client.get_orderbook(sym, depth=5)))

    if tasks:
        results = await asyncio.gather(*[t[2] for t in tasks], return_exceptions=True)
        for (exchange, sym, _), result in zip(tasks, results):
            if isinstance(result, dict):
                bids = result.get("bids", [])
                asks = result.get("asks", [])
                mid = 0.0
                if bids and asks:
                    mid = (float(bids[0][0]) + float(asks[0][0])) / 2
                if mid > 0:
                    exchange_prices.setdefault(sym, {})[exchange] = mid

    for sym, prices in exchange_prices.items():
        exchanges = list(prices.keys())
        for i in range(len(exchanges)):
            for j in range(i + 1, len(exchanges)):
                ea, eb = exchanges[i], exchanges[j]
                pa, pb = prices[ea], prices[eb]
                avg = (pa + pb) / 2
                spread_pct = (pa - pb) / avg * 100 if avg > 0 else 0
                spreads.append({
                    "symbol": sym,
                    "exchange_a": ea,
                    "exchange_b": eb,
                    "price_a": round(pa, 4),
                    "price_b": round(pb, 4),
                    "spread_pct": round(spread_pct, 4),
                    "direction": "a_above_b" if spread_pct > 0 else "a_below_b",
                })

    spreads.sort(key=lambda x: abs(x["spread_pct"]), reverse=True)
    return {"spreads": spreads, "timestamp": time.time()}


@router.get("/cross-arb/funding-rates")
async def get_cross_exchange_funding_rates(
    symbols: str = Query(default=""),
):
    """跨交易所资金费率对比。"""
    mgr = _get_manager()
    sym_list = [s.strip() for s in symbols.split(",") if s.strip()] if symbols else None
    rates = await mgr.get_cross_exchange_funding_rates(sym_list)

    all_symbols: set = set()
    for exchange_rates in rates.values():
        all_symbols.update(exchange_rates.keys())

    comparison = []
    for sym in sorted(all_symbols):
        row = {"symbol": sym}
        for exchange, exchange_rates in rates.items():
            row[exchange] = exchange_rates.get(sym)
        vals = [v for v in row.values() if isinstance(v, (int, float)) and v is not None]
        row["spread"] = round(max(vals) - min(vals), 6) if len(vals) >= 2 else 0
        comparison.append(row)

    comparison.sort(key=lambda x: abs(x.get("spread", 0)), reverse=True)
    return {
        "exchanges": list(rates.keys()),
        "comparison": comparison[:50],
        "timestamp": time.time(),
    }


@router.get("/cross-arb/trades")
async def get_cross_exchange_trades():
    """获取套利交易记录。"""
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import ArbitragePosition
        db = SessionLocal()
        try:
            trades = db.query(ArbitragePosition).order_by(
                ArbitragePosition.id.desc()
            ).limit(50).all()
            return [
                {
                    "id": str(t.id),
                    "symbol": getattr(t, "symbol", ""),
                    "strategy": getattr(t, "strategy", ""),
                    "status": getattr(t, "status", ""),
                    "pnl": float(getattr(t, "pnl", 0) or 0),
                    "mode": getattr(t, "mode", "paper"),
                }
                for t in trades
            ]
        finally:
            db.close()
    except Exception:
        return []


@router.get("/cross-arb/exposure")
async def get_cross_exchange_exposure():
    """获取跨交易所风险敞口。"""
    mgr = _get_manager()
    clients = mgr.get_all_clients()

    total_equity = 0.0
    total_positions_notional = 0.0
    exchange_exposures = []

    tasks = []
    for key, client in clients.items():
        exchange = key.split(":")[0]
        tasks.append((exchange, client.get_balance(), client.get_positions()))

    for exchange, bal_coro, pos_coro in tasks:
        try:
            bal, positions = await asyncio.gather(bal_coro, pos_coro)
            eq = bal.total_equity if isinstance(bal, object) and hasattr(bal, "total_equity") else 0
            notional = sum(p.notional_value for p in positions) if isinstance(positions, list) else 0
            total_equity += eq
            total_positions_notional += notional
            exchange_exposures.append({
                "exchange": exchange,
                "equity": round(eq, 2),
                "positions_notional": round(notional, 2),
                "position_count": len(positions) if isinstance(positions, list) else 0,
            })
        except Exception as e:
            exchange_exposures.append({
                "exchange": exchange,
                "equity": 0,
                "error": str(e),
            })

    return {
        "total_equity": round(total_equity, 2),
        "total_positions_notional": round(total_positions_notional, 2),
        "exposure_pct": round(total_positions_notional / max(total_equity, 1) * 100, 2),
        "exchanges": exchange_exposures,
        "is_safe": total_positions_notional < total_equity * 3,
    }


@router.get("/cross-arb/risk-check")
async def cross_exchange_risk_check():
    """执行跨交易所风控检查（使用 CrossExchangeRiskTracker）。"""
    mgr = _get_manager()
    clients = mgr.get_all_clients()

    if len(clients) < 2:
        return {
            "passed": True,
            "violations": [],
            "message": "Less than 2 exchanges configured; skipping risk check.",
        }

    try:
        from backend.services.exchange.cross_exchange_risk import CrossExchangeRiskTracker
        tracker = CrossExchangeRiskTracker()
    except Exception as e:
        return {"passed": False, "violations": [f"Cannot load risk tracker: {e}"]}

    all_positions: Dict[str, list] = {}
    total_equity = 0.0

    for key, client in clients.items():
        exchange = key.split(":")[0]
        try:
            bal, positions = await asyncio.gather(
                client.get_balance(), client.get_positions()
            )
            total_equity += bal.total_equity
            all_positions[exchange] = positions
        except Exception as e:
            logger.warning("Risk check: %s failed: %s", exchange, e)
            all_positions[exchange] = []

    exchanges = list(all_positions.keys())
    all_violations = []
    all_exposures = []

    for i in range(len(exchanges)):
        for j in range(i + 1, len(exchanges)):
            result = tracker.check_risk(
                all_positions[exchanges[i]],
                all_positions[exchanges[j]],
                total_equity,
            )
            if not result.passed:
                for v in result.violations:
                    all_violations.append(f"[{exchanges[i]}↔{exchanges[j]}] {v}")
            if result.exposure_a:
                all_exposures.append({
                    "exchange": result.exposure_a.exchange,
                    "total_notional": result.exposure_a.total_notional,
                    "position_count": result.exposure_a.position_count,
                })
            if result.exposure_b:
                all_exposures.append({
                    "exchange": result.exposure_b.exchange,
                    "total_notional": result.exposure_b.total_notional,
                    "position_count": result.exposure_b.position_count,
                })

    return {
        "passed": len(all_violations) == 0,
        "violations": all_violations,
        "total_equity": round(total_equity, 2),
        "exposures": all_exposures,
        "rules": tracker.rules,
    }


# ════════════════════════════════════════════════════════
#  V3 新增端点
# ════════════════════════════════════════════════════════

@router.get("/metrics")
async def get_position_metrics():
    """获取实时仓位监控指标"""
    orch = _get_orchestrator()
    if orch is None:
        return {"metrics": [], "actions": []}

    try:
        metrics = orch._last_metrics
        return {
            "count": len(metrics),
            "metrics": [
                {
                    "position_id": m.position_id,
                    "current_delta": round(m.current_delta, 4),
                    "delta_pct": round(m.delta_pct, 4),
                    "unrealized_pnl": round(m.unrealized_pnl, 4),
                    "accumulated_funding": round(m.accumulated_funding, 4),
                    "funding_trend": m.funding_trend,
                    "z_score_current": round(m.z_score_current, 4),
                    "liquidation_distance_pct": round(m.liquidation_distance_pct, 2),
                    "age_hours": round(m.age_hours, 1),
                    "edge_decay_pct": round(m.edge_decay_pct, 2),
                    "total_pnl": round(m.total_pnl, 4),
                }
                for m in metrics
            ],
        }
    except Exception as e:
        return {"count": 0, "metrics": [], "error": str(e)}


@router.get("/capital-pool")
async def get_capital_pool():
    """获取资金池状态"""
    orch = _get_orchestrator()
    if orch is None:
        return {"error": "orchestrator not available"}

    pool = orch.capital_pool
    return {
        "total_pool_usd": round(pool.total_pool_usd, 2),
        "allocated_usd": round(pool.allocated_usd, 2),
        "available_usd": round(pool.available_usd, 2),
        "utilization_pct": round(pool.utilization_pct * 100, 1),
        "daily_loss_limit_pct": pool.daily_loss_limit_pct,
        "daily_realized_loss": round(pool.daily_realized_loss, 2),
        "max_pool_pct_of_equity": pool.max_pool_pct_of_equity,
    }


@router.get("/performance")
async def get_arbitrage_performance():
    """获取套利综合绩效统计"""
    from backend.database.connection import SessionLocal

    db = SessionLocal()
    try:
        from backend.database.models import ArbitragePosition
        from sqlalchemy import func

        total = db.query(func.count(ArbitragePosition.id)).scalar() or 0
        active = db.query(func.count(ArbitragePosition.id)).filter(
            ArbitragePosition.status == "active"
        ).scalar() or 0
        closed = db.query(func.count(ArbitragePosition.id)).filter(
            ArbitragePosition.status == "closed"
        ).scalar() or 0

        # 统计各策略类型
        strategy_counts = {}
        for row in db.query(
            ArbitragePosition.strategy,
            func.count(ArbitragePosition.id),
        ).group_by(ArbitragePosition.strategy).all():
            strategy_counts[row[0]] = row[1]

        return {
            "total_positions": total,
            "active_positions": active,
            "closed_positions": closed,
            "strategy_breakdown": strategy_counts,
        }
    except Exception as e:
        return {"error": str(e)}
    finally:
        db.close()


@router.post("/close/{position_id}")
async def close_arbitrage_position(position_id: str, reason: str = "manual"):
    """手动平仓"""
    orch = _get_orchestrator()
    if orch is None:
        raise HTTPException(status_code=503, detail="Orchestrator not available")

    pos = orch.active_positions.get(position_id)
    if pos is None:
        raise HTTPException(status_code=404, detail=f"Position {position_id} not found")

    orch._close_position(position_id, reason)
    return {"ok": True, "position_id": position_id, "reason": reason}


class ModeSwitchRequest(BaseModel):
    mode: str  # "paper" or "live"
    confirm: bool = False

@router.put("/mode")
async def set_arbitrage_mode(req: ModeSwitchRequest):
    """切换 paper/live 模式（需要 confirm=true）"""
    if not req.confirm:
        raise HTTPException(
            status_code=400,
            detail="Must set confirm=true to switch mode"
        )

    orch = _get_orchestrator()
    if orch is None:
        raise HTTPException(status_code=503, detail="Orchestrator not available")

    success = orch.set_mode(req.mode)
    if not success:
        raise HTTPException(status_code=400, detail=f"Invalid mode: {req.mode}")

    logger.warning(f"[ArbitrageAPI] 模式切换: {req.mode} (confirmed)")
    return {"ok": True, "mode": req.mode}


@router.get("/basis/opportunities")
async def get_basis_opportunities():
    """获取基差套利机会"""
    try:
        from backend.services.arbitrage.basis_arb_executor import BasisArbExecutor
        executor = BasisArbExecutor()
        # 获取最近的基差快照
        snapshots = []
        for symbol, history in executor._basis_history.items():
            if history:
                latest = history[-1]
                snapshots.append({
                    "symbol": symbol,
                    "spot_price": latest.spot_price,
                    "perp_price": latest.perp_price,
                    "basis_pct": round(latest.basis_pct, 6),
                    "timestamp": latest.timestamp,
                })
        return {"count": len(snapshots), "opportunities": snapshots}
    except Exception as e:
        return {"count": 0, "opportunities": [], "error": str(e)}


@router.get("/alerts")
async def get_arbitrage_alerts(
    since: float = Query(0, description="Unix timestamp，只返回此时间之后的告警"),
    limit: int = Query(50, ge=1, le=200),
    code: Optional[str] = Query(None, description="过滤告警代码"),
):
    """获取套利监控告警（单腿失败/资金池/费率突变/熔断）"""
    from backend.services.arbitrage.arbitrage_alert_monitor import arb_alert_monitor

    return {
        "summary": arb_alert_monitor.get_summary(),
        "alerts": arb_alert_monitor.get_alerts(since=since, limit=limit, code=code),
    }


@router.get("/mid-cache/status")
async def get_mid_cache_status():
    """跨所 mid price 缓存 + WS feed + MarketDataHub 状态"""
    from backend.services.arbitrage.cross_exchange_mid_cache import mid_cache
    from backend.services.arbitrage.cross_exchange_ws_feed import cross_exchange_ws_feed

    status = mid_cache.get_status()
    status["ws_feed"] = cross_exchange_ws_feed.get_status()
    try:
        from backend.services.market_data_hub import market_data_hub
        status["market_data_hub"] = market_data_hub.get_status()
    except Exception:
        status["market_data_hub"] = {}
    return status


@router.get("/market-data-hub/status")
async def get_market_data_hub_status():
    """MarketDataHub 统一 WS 行情总线状态"""
    from backend.services.market_data_hub import market_data_hub

    return market_data_hub.get_status()


@router.get("/execution-authority")
async def get_execution_authority_status():
    """执行权威状态 — FullAuto 为唯一自动执行路径"""
    from backend.services.arbitrage.execution_authority import execution_authority

    return execution_authority.get_status()
