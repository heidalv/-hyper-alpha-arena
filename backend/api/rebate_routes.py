"""
Rebate Arb API Routes — 积分/返利套利系统 REST API

端点:
  GET  /status              系统状态
  GET  /opportunities       当前 S1-S8 策略机会
  GET  /positions           活跃仓位
  POST /positions/{id}/close 手动平仓
  POST /execute             执行指定策略
  GET  /capital             资金池分配状态
  GET  /wash-trade/status   刷量规避器状态
  GET  /analytics           绩效分析
  POST /scan                手动触发扫描
  POST /emergency/close-all 紧急全平
  GET  /incentives          交易所激励汇总
  GET  /funding-matrix      实时多场所资金费矩阵 + delta-neutral 净EV机会
  GET  /incentives/freshness 数据新鲜度报告
  POST /incentives/refresh  手动刷新激励数据
  GET  /risk/breakers       熔断器状态
  POST /risk/breakers/reset 重置熔断器
  GET  /reconcile           触发仓位对账
  PATCH /mode               切换 paper/live 模式
  GET  /config              当前配置
"""

import time
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.core.permissions import require_feature

logger = logging.getLogger(__name__)
# [阶段4 Task 4.4] 整组挂 require_feature("rebate"):rebate 属于
# RESTRICTED_FEATURES,仅 vip / admin 可用;free/pro → 403(三维权限-功能门控)。
router = APIRouter(dependencies=[Depends(require_feature("rebate"))])


# ── Pydantic Request Models ──

class ExecuteStrategyRequest(BaseModel):
    strategy_type: str  # S1-S8
    size_usd: float
    symbol: str = ""
    mode: Optional[str] = None  # "paper" / "live" / None


class SwitchModeRequest(BaseModel):
    mode: str  # "paper" or "live"
    paper_account_id: Optional[int] = None


class StrategyConfigPatch(BaseModel):
    params: Optional[Dict[str, Any]] = None
    risk_overrides: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None


class EngineConfigPatch(BaseModel):
    min_monthly_value: Optional[float] = None
    max_position_usd: Optional[float] = None
    max_total_volume_7d: Optional[float] = None
    max_holding_days: Optional[float] = None


class RiskGateConfigPatch(BaseModel):
    max_daily_volume_per_exchange: Optional[float] = None
    max_weekly_volume_per_exchange: Optional[float] = None
    min_active_days_per_week: Optional[int] = None
    max_wash_trade_score: Optional[float] = None
    max_single_exchange_exposure_pct: Optional[float] = None
    max_total_rebate_exposure_pct: Optional[float] = None
    min_volume_value_ratio: Optional[float] = None
    campaign_critical_days: Optional[int] = None
    max_daily_loss_pct: Optional[float] = None
    max_fee_change_pct: Optional[float] = None


class AiConfigGenerateRequest(BaseModel):
    risk_profile: str           # "conservative" | "balanced" | "aggressive"
    total_equity: float
    target_exchanges: List[str] = []
    goal: str = ""


class RuleGatePauseRequest(BaseModel):
    strategies: List[str] = []
    reason: str = "manual_rule_sync_pause"
    rebate_pause: bool = True
    v3_pause: bool = False
    requires_code_change: bool = False
    allow_manual_override: Optional[bool] = None
    actor_user_id: Optional[int] = None
    risk_acknowledged: bool = False


class RuleGateResumeRequest(BaseModel):
    reason: str = "manual_rule_sync_resume"
    actor_user_id: Optional[int] = None
    risk_acknowledged: bool = True


class RuleSnapshotIngestRequest(BaseModel):
    source_id: str
    content_text: str
    title: Optional[str] = None
    url: Optional[str] = None


class RuleStatusPatch(BaseModel):
    status: str


# ════════════════════════════════════════════════════════
#  GET /status — 系统状态
# ════════════════════════════════════════════════════════

@router.get("/status")
async def get_rebate_status():
    """积分/返利套利引擎状态"""
    try:
        from backend.services.rebate_arb.engine import rebate_arb_engine
        from backend.services.rebate_arb.wash_trade_avoider import wash_trade_avoider

        engine_status = rebate_arb_engine.get_status()
        wash_check = wash_trade_avoider.check_timing()

        return {
            "engine_enabled": True,
            "mode": engine_status.get("mode", "paper"),
            "scan_count": engine_status.get("scan_count", 0),
            "execution_count": engine_status.get("execution_count", 0),
            "active_positions": engine_status.get("active_positions", 0),
            "total_rebate_pnl": round(engine_status.get("total_rebate_pnl", 0.0), 2),
            "wash_trade_safe": wash_check[0],
            "next_safe_interval_sec": round(wash_check[1], 1),
        }
    except Exception as e:
        return {"engine_enabled": False, "error": str(e)}


# ════════════════════════════════════════════════════════
#  GET /opportunities — 当前 S1-S8 策略机会
# ════════════════════════════════════════════════════════

@router.get("/opportunities")
async def get_rebate_opportunities():
    """扫描当前 S1-S8 策略机会"""
    try:
        from backend.services.rebate_arb.engine import rebate_arb_engine

        # 使用空激励数据触发扫描（实际场景下由 FullAuto 注入真实数据）
        incentive_data = _get_cached_incentive_data()
        funding_rates = _get_cached_funding_rates()
        account_equity = _get_account_equity()

        evaluations = rebate_arb_engine.scan_all_strategies(
            incentive_data, funding_rates, account_equity
        )

        opportunities = []
        for ev in evaluations:
            opportunities.append({
                "strategy_type": ev.strategy_type.value,
                "is_viable": ev.is_viable,
                "expected_monthly_value": round(ev.expected_monthly_value, 2),
                "required_volume_usd": round(ev.required_volume_usd, 2),
                "risk_score": round(ev.risk_score, 4),
                "confidence": round(ev.confidence, 4),
                "volume_value_ratio": round(ev.volume_value_ratio, 4),
                "details": ev.details,
            })

        return {"count": len(opportunities), "opportunities": opportunities}
    except Exception as e:
        return {"count": 0, "opportunities": [], "error": str(e)}


# ════════════════════════════════════════════════════════
#  GET /positions — 活跃仓位
# ════════════════════════════════════════════════════════

@router.get("/positions")
async def get_rebate_positions(status: str = Query("active", description="仓位状态过滤")):
    """获取返利套利仓位列表"""
    try:
        from backend.services.rebate_arb.position_monitor import rebate_position_monitor
        from backend.services.rebate_arb.models import RebatePositionStatus

        if status == "active":
            positions = rebate_position_monitor.get_active_positions()
        else:
            positions = rebate_position_monitor.get_active_positions()

        from backend.services.rebate_arb.rebate_position_mtm import (
            refresh_all_paper_positions_mtm,
            serialize_position_for_api,
        )

        refresh_all_paper_positions_mtm()

        result = []
        for p in positions:
            if status and p.status.value != status:
                continue
            result.append(serialize_position_for_api(p))

        return {"count": len(result), "positions": result}
    except Exception as e:
        return {"count": 0, "positions": [], "error": str(e)}


@router.get("/unified-positions")
async def get_unified_positions(status: str = "all"):
    """统一仓位视图：Rebate/S1-S8 + V3 合约套利。"""
    try:
        import json
        from backend.database.connection import SessionLocal
        from backend.database.models import RebatePositionDB
        from backend.services.rebate_arb.engine import rebate_arb_engine

        items: List[Dict[str, Any]] = []
        db = SessionLocal()
        try:
            query = db.query(RebatePositionDB)
            if status != "all":
                if status == "active":
                    query = query.filter(RebatePositionDB.status.in_(["active", "holding"]))
                else:
                    query = query.filter(RebatePositionDB.status == status)
            for p in query.order_by(RebatePositionDB.entry_time.desc()).limit(300).all():
                try:
                    meta = json.loads(p.metadata_json or "{}")
                except Exception:
                    meta = {}
                items.append({
                    "id": p.position_id,
                    "source": "rebate",
                    "strategy_type": p.strategy_type,
                    "symbol": p.symbol,
                    "exchange_a": p.source_exchange,
                    "exchange_b": p.target_exchange,
                    "side_a_size": p.side_a_size,
                    "side_b_size": p.side_b_size,
                    "notional_usd": abs(p.side_a_size or 0) + abs(p.side_b_size or 0),
                    "pnl": p.current_pnl,
                    "rebate": p.accumulated_rebate,
                    "points": p.accumulated_points,
                    "status": p.status,
                    "paper_mode": p.paper_mode,
                    "entry_time": p.entry_time,
                    "close_time": p.close_time,
                    "metadata": meta,
                })
        finally:
            db.close()

        # Include in-memory V3 positions as a best-effort contract arbitrage view.
        try:
            for p in rebate_arb_engine.get_all_positions():
                if p.position_id not in {item["id"] for item in items}:
                    if status != "all" and p.status.value != status:
                        continue
                    items.append({
                        "id": p.position_id,
                        "source": "rebate_memory",
                        "strategy_type": p.strategy_type.value,
                        "symbol": p.symbol,
                        "exchange_a": p.source_exchange,
                        "exchange_b": p.target_exchange,
                        "side_a_size": p.side_a_size,
                        "side_b_size": p.side_b_size,
                        "notional_usd": abs(p.side_a_size or 0) + abs(p.side_b_size or 0),
                        "pnl": p.current_pnl,
                        "rebate": p.accumulated_rebate,
                        "points": p.accumulated_points,
                        "status": p.status.value,
                        "paper_mode": p.paper_mode,
                        "entry_time": p.entry_time,
                        "close_time": None,
                        "metadata": {},
                    })
        except Exception:
            pass

        try:
            from backend.services.arbitrage.orchestrator import arbitrage_orchestrator
            for p in getattr(arbitrage_orchestrator, "_positions", {}).values():
                p_status = getattr(p, "status", "active")
                if status != "all" and p_status != status:
                    continue
                items.append({
                    "id": getattr(p, "position_id", ""),
                    "source": "v3",
                    "strategy_type": getattr(p, "strategy", "v3"),
                    "symbol": getattr(p, "symbol", ""),
                    "exchange_a": "long",
                    "exchange_b": "short",
                    "side_a_size": getattr(p, "long_size", 0),
                    "side_b_size": getattr(p, "short_size", 0),
                    "notional_usd": abs(getattr(p, "long_size", 0) or 0) + abs(getattr(p, "short_size", 0) or 0),
                    "pnl": getattr(p, "accumulated_funding", 0),
                    "rebate": 0,
                    "points": 0,
                    "status": p_status,
                    "paper_mode": False,
                    "entry_time": getattr(p, "entry_time", None),
                    "close_time": getattr(p, "close_time", None),
                    "metadata": {"delta": getattr(p, "delta", None)},
                })
        except Exception:
            pass

        items.sort(key=lambda x: x.get("entry_time") or 0, reverse=True)
        return {"count": len(items), "positions": items}
    except Exception as e:
        return {"count": 0, "positions": [], "error": str(e)}


# ════════════════════════════════════════════════════════
#  POST /positions/{position_id}/close — 手动平仓
# ════════════════════════════════════════════════════════

@router.post("/positions/{position_id}/close")
async def close_rebate_position(position_id: str, reason: str = "manual"):
    """手动平仓指定仓位（经 ExecutionAuthority）"""
    try:
        from backend.services.arbitrage.execution_authority import (
            ExecutionAuthority,
            ExecutionSource,
        )

        return ExecutionAuthority.close_rebate_position(
            position_id, reason=reason, source=ExecutionSource.API
        )
    except Exception as e:
        return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════════
#  GET /capital — 资金池分配状态
# ════════════════════════════════════════════════════════

@router.get("/capital")
async def get_rebate_capital():
    """获取资金池分配和利用状态"""
    try:
        from backend.services.rebate_arb.capital_coordinator import capital_coordinator

        utilization = capital_coordinator.get_all_utilization()
        allocation = capital_coordinator._allocation

        return {
            "total_equity": allocation.total_equity,
            "allocations": {
                k: round(v, 2) for k, v in allocation.allocations.items()
            },
            "used": {
                k: round(v, 2) for k, v in allocation.used.items()
            },
            "utilization": {
                k: round(v * 100, 1) for k, v in utilization.items()
            },
            "rebate_available": round(allocation.available_for_rebate, 2),
            "total_utilization_pct": round(allocation.total_utilization * 100, 1),
        }
    except Exception as e:
        return {"error": str(e)}


# ════════════════════════════════════════════════════════
#  GET /wash-trade/status — 刷量规避器状态
# ════════════════════════════════════════════════════════

@router.get("/wash-trade/status")
async def get_wash_trade_status():
    """获取刷量规避器当前状态"""
    try:
        from backend.services.rebate_arb.wash_trade_avoider import wash_trade_avoider

        is_safe, next_interval = wash_trade_avoider.check_timing()

        # 获取最近交易统计
        daily_vol = sum(wash_trade_avoider._daily_volumes.values()) if wash_trade_avoider._daily_volumes else 0

        return {
            "is_safe": is_safe,
            "next_safe_interval_sec": round(next_interval, 1),
            "daily_volume_usd": round(daily_vol, 2),
            "last_trade_ts": wash_trade_avoider._last_trade_ts,
            "trade_count_today": len([
                t for t in wash_trade_avoider._trade_history
                if t.get("timestamp", 0) > time.time() - 86400
            ]),
            "risk_level": "low" if is_safe else "high",
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/wash-trade/timeline")
async def get_wash_trade_timeline(limit: int = Query(100, ge=1, le=500)):
    """获取刷交易安全时间线。"""
    try:
        import json
        from backend.database.connection import SessionLocal
        from backend.database.models import WashTradeLogDB
        from backend.services.rebate_arb.wash_trade_avoider import wash_trade_avoider

        db = SessionLocal()
        try:
            rows = db.query(WashTradeLogDB).order_by(WashTradeLogDB.ts.desc()).limit(limit).all()
            timeline = []
            for row in rows:
                try:
                    meta = json.loads(row.metadata_json or "{}")
                except Exception:
                    meta = {}
                timeline.append({
                    "id": row.id,
                    "ts": row.ts,
                    "exchange": row.exchange,
                    "strategy_type": row.strategy_type,
                    "size_usd": row.size_usd,
                    "risk_score": row.risk_score,
                    "is_safe": row.is_safe,
                    "reason": row.reason,
                    "metadata": meta,
                })
        finally:
            db.close()

        if not timeline:
            timeline = [
                {
                    "id": idx,
                    "ts": item.get("ts", 0),
                    "exchange": item.get("exchange", ""),
                    "strategy_type": item.get("strategy_type", ""),
                    "size_usd": item.get("size_usd", 0),
                    "risk_score": item.get("risk_score", 0),
                    "is_safe": item.get("is_safe", True),
                    "reason": item.get("reason", ""),
                    "metadata": {},
                }
                for idx, item in enumerate(reversed(wash_trade_avoider._trade_history[-limit:]))
            ]
        return {"count": len(timeline), "timeline": timeline}
    except Exception as e:
        return {"count": 0, "timeline": [], "error": str(e)}


# ════════════════════════════════════════════════════════
#  GET /analytics — 绩效分析
# ════════════════════════════════════════════════════════

@router.get("/analytics")
async def get_rebate_analytics():
    """获取积分返利套利绩效分析（优先 DB 持久化数据，重启后仍准确）"""
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import RebatePositionDB, RebatePerformanceLogDB
        from backend.services.rebate_arb.engine import rebate_arb_engine
        from backend.services.rebate_arb.points_aggregation import build_db_performance_summary

        db = SessionLocal()
        try:
            active = db.query(RebatePositionDB).filter(
                RebatePositionDB.status.in_(["active", "holding"])
            ).all()
            all_positions = db.query(RebatePositionDB).all()
            logs = db.query(RebatePerformanceLogDB).all()
            performance = build_db_performance_summary(
                active, logs, pos_lookup=all_positions
            )
        finally:
            db.close()

        perf = rebate_arb_engine.get_performance_summary()
        return {
            "engine_mode": perf.get("engine_mode", "paper") if isinstance(perf, dict) else "paper",
            "scan_count": perf.get("scan_count", 0) if isinstance(perf, dict) else 0,
            "execution_count": perf.get("execution_count", 0) if isinstance(perf, dict) else 0,
            "active_positions": perf.get("active_positions", 0) if isinstance(perf, dict) else 0,
            "total_trades": performance.get("total_trades", 0),
            "win_rate": performance.get("win_rate", 0.0),
            "total_pnl": performance.get("total_pnl", 0.0),
            "total_rebate": performance.get("total_rebate", 0.0),
            "total_points": performance.get("total_points", 0.0),
            "net_pnl": performance.get("net_pnl", 0.0),
            "by_strategy": performance.get("by_strategy", {}),
            "active_unrealized_pnl": performance.get("active_unrealized_pnl", 0.0),
            "active_accrued_points": performance.get("active_accrued_points", 0.0),
            "data_source": performance.get("source", "database"),
            "raw": perf,
        }
    except Exception as e:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "total_rebate": 0.0,
            "total_points": 0.0,
            "net_pnl": 0.0,
            "by_strategy": {},
            "error": str(e),
        }


# ════════════════════════════════════════════════════════
#  POST /scan — 手动触发扫描
# ════════════════════════════════════════════════════════

@router.post("/scan")
async def trigger_rebate_scan():
    """手动触发积分返利策略扫描（经 ExecutionAuthority）"""
    try:
        from backend.services.arbitrage.execution_authority import (
            ExecutionAuthority,
            ExecutionSource,
        )

        return ExecutionAuthority.scan_rebate_strategies(source=ExecutionSource.API)
    except Exception as e:
        return {"triggered": False, "error": str(e)}


# ════════════════════════════════════════════════════════
#  POST /emergency/close-all — 紧急全平
# ════════════════════════════════════════════════════════

@router.post("/emergency/close-all")
async def emergency_close_all(reason: str = "manual_emergency"):
    """紧急平仓所有返利仓位（经 ExecutionAuthority）"""
    try:
        from backend.services.arbitrage.execution_authority import (
            ExecutionAuthority,
            ExecutionSource,
        )

        return ExecutionAuthority.close_all_rebate_positions(
            reason=reason, source=ExecutionSource.API
        )
    except Exception as e:
        return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════════
#  GET /incentives — 交易所激励汇总
# ════════════════════════════════════════════════════════

@router.get("/incentives")
async def get_exchange_incentives():
    """获取所有交易所激励数据汇总（费率/积分/返利/活动）"""
    try:
        from backend.services.exchange.exchange_manager import get_exchange_manager

        summaries = []
        manager = get_exchange_manager()
        if not manager:
            return {"count": 0, "exchanges": [], "error": "ExchangeManager not initialized"}

        clients = manager.get_all_clients()
        for exchange_name, client in clients.items():
            try:
                incentive = client.get_incentive_summary()
                summaries.append({
                    "exchange": exchange_name,
                    "is_connected": True,
                    "fee_tier": {
                        "tier_name": incentive.fee_tier.tier_name,
                        "maker_rate": incentive.fee_tier.maker_rate,
                        "taker_rate": incentive.fee_tier.taker_rate,
                        "rebate_rate": incentive.fee_tier.rebate_rate,
                        "effective_taker_cost": incentive.fee_tier.effective_taker_cost,
                        "net_maker_rate": incentive.fee_tier.net_maker_rate,
                    },
                    "points": {
                        "points_balance": incentive.points.points_balance,
                        "points_multiplier": incentive.points.points_multiplier,
                        "daily_points_rate": incentive.points.daily_points_rate,
                        "airdrop_eligible": incentive.points.airdrop_eligible,
                        "estimated_airdrop_value": incentive.points.estimated_airdrop_value,
                        "qualification_pct": incentive.points.qualification_pct,
                    },
                    "rebate": {
                        "current_rebate_rate": incentive.rebate.current_rebate_rate,
                        "projected_weekly_rebate": incentive.rebate.projected_weekly_rebate,
                    },
                    "total_estimated_monthly_value": incentive.total_estimated_monthly_value,
                })
            except Exception as e:
                summaries.append({
                    "exchange": exchange_name,
                    "is_connected": False,
                    "error": str(e),
                })

        return {"count": len(summaries), "exchanges": summaries}
    except Exception as e:
        return {"count": 0, "exchanges": [], "error": str(e)}


# ════════════════════════════════════════════════════════
#  GET /funding-matrix — 实时多场所资金费矩阵 + delta-neutral 净EV机会
# ════════════════════════════════════════════════════════

@router.get("/funding-matrix")
async def get_funding_matrix(
    horizon_days: float = Query(7.0, ge=0.5, le=60.0, description="假设持有天数（摊销手续费/年化）"),
    use_taker: bool = Query(True, description="True=taker费保守估成本；False=乐观maker"),
    min_net_apr: float = Query(-1e9, description="只返回净年化>=该阈值的组合（默认全取）"),
):
    """返回实时资金费矩阵与 delta-neutral 净 EV 机会。

    数据来源：perp_funding 表（多场所采集器写入）→ funding_rate_provider。
    绝不臆造：无数据时 venues/matrix/combos 均为空，multi_venue=false。

    返回:
      - as_of: 数据快照时间戳（秒）
      - multi_venue: 是否≥2场所对同一symbol有费率（可凑双腿）
      - venues: {exchange: [覆盖的symbol,...]}
      - matrix: [{symbol, venues:{exchange: hourly_rate}}]（每symbol一行）
      - combos: 每symbol最优 delta-neutral 组合（按净年化降序），含净EV/保本天数
    """
    try:
        from backend.services.rebate_arb.funding_rate_provider import (
            latest_funding_by_venue,
            has_multi_venue_coverage,
        )
        from backend.services.rebate_arb.funding_rate_matrix import scan_funding_matrix

        by_venue = latest_funding_by_venue(use_cache=False)
        multi = has_multi_venue_coverage(by_venue)

        venues = {ex: sorted(m.keys()) for ex, m in (by_venue or {}).items() if m}

        # 组装 per-symbol 矩阵行：{symbol: {exchange: rate}}
        matrix_by_symbol: Dict[str, Dict[str, float]] = {}
        for ex, m in (by_venue or {}).items():
            for sym, rate in (m or {}).items():
                if rate is None:
                    continue
                matrix_by_symbol.setdefault(sym, {})[ex] = float(rate)
        matrix = [
            {"symbol": sym, "venues": row}
            for sym, row in sorted(matrix_by_symbol.items())
        ]

        combos: List[Dict[str, Any]] = []
        if multi:
            scanned = scan_funding_matrix(
                by_venue,
                horizon_days=horizon_days,
                use_taker=use_taker,
                prefer_points_long=True,
                min_net_apr=min_net_apr,
            )
            combos = [c.to_dict() for c in scanned]

            # 叠加 SDN 自适应持有期视角：保本期超默认窗口时延长持有（封顶），
            # 给出该持有期下的净年化与是否达到 SDN 可行阈值（诚实、可直接展示）。
            try:
                from backend.services.rebate_arb.strategies.s_delta_neutral_points import (
                    DeltaNeutralPointsStrategy,
                )

                _sdn = DeltaNeutralPointsStrategy()
                _min_apr = float(_sdn.MIN_NET_APR)
                for c in combos:
                    nfpd = float(c.get("net_funding_per_day", 0.0) or 0.0)
                    fee_drag = float(c.get("fee_drag", 0.0) or 0.0)
                    eh = _sdn._adaptive_horizon(c)
                    net_apr_eh = (
                        (nfpd * eh - fee_drag) * (365.0 / eh) if eh > 0 else 0.0
                    )
                    c["sdn_horizon_days"] = round(eh, 2)
                    c["sdn_horizon_adaptive"] = bool(eh > _sdn.HORIZON_DAYS + 1e-9)
                    c["sdn_net_apr"] = round(net_apr_eh, 4)
                    c["sdn_viable"] = bool(
                        net_apr_eh >= _min_apr
                        and c.get("long_exchange") != c.get("short_exchange")
                    )
                    c["sdn_min_net_apr"] = _min_apr
            except Exception as _sdn_err:
                logger.debug("funding-matrix SDN 标记失败（非致命）: %s", _sdn_err)

            combos.sort(
                key=lambda c: c.get("net_apr_at_horizon", 0.0) or 0.0, reverse=True
            )

        return {
            "as_of": time.time(),
            "multi_venue": multi,
            "horizon_days": horizon_days,
            "use_taker": use_taker,
            "venue_count": len(venues),
            "symbol_count": len(matrix),
            "combo_count": len(combos),
            "venues": venues,
            "matrix": matrix,
            "combos": combos,
        }
    except Exception as e:
        logger.error("get_funding_matrix failed: %s", e)
        return {
            "as_of": time.time(),
            "multi_venue": False,
            "venues": {},
            "matrix": [],
            "combos": [],
            "error": str(e),
        }


# ════════════════════════════════════════════════════════
#  GET /funding-collector/status — 多场所采集器健康度（各场所连通状态）
# ════════════════════════════════════════════════════════

@router.get("/funding-collector/status")
async def get_funding_collector_status():
    """返回多场所资金费采集器的运行配置 + 最近一轮各场所健康快照。

    数据来源：multi_venue_funding_collector 的内存快照（最近一次 collect_once）。
    诚实：从未采集过 → last_report 为空、has_report=false；不臆造任何状态。

    返回:
      - enabled: 采集器是否开启（MULTI_VENUE_FUNDING_COLLECTOR_ENABLED）
      - interval_seconds: 采集间隔
      - alert_threshold: 连续失败告警阈值（0=关闭）
      - has_report: 是否已有过采集快照
      - offline / rows_written / symbols_covered / venues_with_data / as_of_iso
      - venue_report: {venue: {status, count, elapsed_ms, via, error?}}
      - consecutive_failures: {venue: 连续失败轮数}
      - alerted_venues: 当前处于告警态（已飞书报过、未恢复）的场所
    """
    try:
        from backend.config import settings
        from backend.services import multi_venue_funding_collector as mvc

        report = mvc.get_last_report()
        return {
            "enabled": bool(
                getattr(settings, "MULTI_VENUE_FUNDING_COLLECTOR_ENABLED", False)
            ),
            "interval_seconds": int(
                getattr(settings, "MULTI_VENUE_FUNDING_COLLECT_INTERVAL_SECONDS", 300)
            ),
            "alert_threshold": int(
                getattr(settings, "MULTI_VENUE_FUNDING_ALERT_THRESHOLD", 3)
            ),
            "has_report": bool(report),
            "offline": report.get("offline"),
            "rows_written": report.get("rows_written"),
            "symbols_covered": report.get("symbols_covered", []),
            "venues_with_data": report.get("venues_with_data", []),
            "as_of": report.get("as_of"),
            "as_of_iso": report.get("as_of_iso"),
            "elapsed_ms": report.get("elapsed_ms"),
            "venue_report": report.get("venue_report", {}),
            "consecutive_failures": dict(mvc._CONSEC_FAIL),
            "alerted_venues": sorted(mvc._ALERTED_VENUES),
        }
    except Exception as e:
        logger.error("get_funding_collector_status failed: %s", e)
        return {
            "enabled": False,
            "has_report": False,
            "venue_report": {},
            "error": str(e),
        }


# ════════════════════════════════════════════════════════
#  GET /incentives/freshness — 数据新鲜度报告
# ════════════════════════════════════════════════════════

@router.get("/incentives/freshness")
async def get_incentive_freshness():
    """获取各交易所激励数据新鲜度和健康状态"""
    try:
        from backend.services.rebate_arb.incentive_aggregator import incentive_aggregator
        from backend.services.rebate_arb.incentive_cache import incentive_cache

        freshness = incentive_aggregator.get_freshness_report()
        cache_stats = incentive_cache.get_stats()
        agg_stats = incentive_aggregator.get_stats()

        return {
            "exchanges": freshness,
            "cache": cache_stats,
            "aggregator": agg_stats,
        }
    except Exception as e:
        return {"error": str(e)}


# ════════════════════════════════════════════════════════
#  GET /programs — 积分项目生命周期 + 状态（前端置灰死项目）
# ════════════════════════════════════════════════════════

@router.get("/programs")
async def get_points_programs():
    """[2026-07-06 Phase4] 返回所有积分项目的生命周期与状态。

    前端据此展示"净资金费/积分/程序状态"，并把 status != active 的死项目置灰。
    数据来自离线权威 program_registry（本环境无法联网抓取，人工维护）。
    """
    try:
        from backend.services.rebate_arb import program_registry as pr

        programs = [p.to_dict() for p in pr.all_programs()]
        # 按"是否 active"排序，active 在前
        programs.sort(key=lambda x: (not x.get("is_active_now"), x.get("program_id", "")))
        active = [p for p in programs if p.get("is_active_now")]
        return {
            "count": len(programs),
            "active_count": len(active),
            "programs": programs,
            "note": "status!=active 的项目应在前端置灰；数据源为离线 program_registry",
        }
    except Exception as e:
        return {"count": 0, "programs": [], "error": str(e)}


# ════════════════════════════════════════════════════════
#  GET /arb-switches — 统一套利开关状态（单一事实来源）
# ════════════════════════════════════════════════════════

@router.get("/arb-switches")
async def get_arb_switches(session_arb_enabled: bool = Query(False)):
    """[2026-07-06 Phase3] 返回 V3 统计套利 与 Rebate/delta-neutral 两条链路的开关语义，
    根治"开了套利但 V3 不动"的排查困惑。"""
    try:
        from backend.services.rebate_arb.arb_switches import get_arb_switch_status

        return get_arb_switch_status(session_arb_enabled).to_dict()
    except Exception as e:
        return {"error": str(e)}


# ════════════════════════════════════════════════════════
#  POST /incentives/refresh — 手动刷新激励数据
# ════════════════════════════════════════════════════════

@router.post("/incentives/refresh")
async def refresh_incentive_data():
    """手动触发激励数据刷新（调用 IncentiveAggregator.fetch_all）"""
    try:
        from backend.services.rebate_arb.incentive_aggregator import incentive_aggregator

        data = await incentive_aggregator.fetch_all()
        return {
            "success": True,
            "exchanges_fetched": len(data),
            "exchanges": list(data.keys()),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════════
#  POST /execute — 执行指定策略
# ════════════════════════════════════════════════════════

@router.post("/execute")
async def execute_strategy(req: ExecuteStrategyRequest):
    """执行指定策略（经 ExecutionAuthority 统一路由）"""
    try:
        from backend.services.arbitrage.execution_authority import (
            ExecutionAuthority,
            ExecutionSource,
        )

        return ExecutionAuthority.execute_rebate_strategy(
            strategy_type=req.strategy_type,
            size_usd=req.size_usd,
            symbol=req.symbol,
            mode=req.mode,
            source=ExecutionSource.API,
        )
    except Exception as e:
        return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════════
#  GET /risk/breakers — 熔断器状态
# ════════════════════════════════════════════════════════

@router.get("/risk/breakers")
async def get_circuit_breakers():
    """获取风控熔断器当前状态"""
    try:
        from backend.services.rebate_arb.risk_gate import rebate_risk_gate
        from backend.services.rebate_arb.rule_sync_gate import rule_sync_gate

        active = rebate_risk_gate.circuit_breaker.get_active_breakers()
        is_tripped = rebate_risk_gate.circuit_breaker.is_tripped()
        gate = rule_sync_gate.get_state()

        return {
            "is_tripped": is_tripped or gate.get("is_rebate_paused") or gate.get("is_v3_paused"),
            "active_breakers": active,
            "rule_sync_gate": gate,
            "count": len(active),
        }
    except Exception as e:
        return {"is_tripped": False, "active_breakers": {}, "error": str(e)}


# ════════════════════════════════════════════════════════
#  POST /risk/breakers/reset — 重置熔断器
# ════════════════════════════════════════════════════════

@router.post("/risk/breakers/reset")
async def reset_circuit_breakers(rule_id: str = ""):
    """手动重置熔断器（空 rule_id = 全部重置）"""
    try:
        from backend.services.rebate_arb.risk_gate import rebate_risk_gate

        rebate_risk_gate.circuit_breaker.reset(rule_id)
        return {
            "success": True,
            "reset_rule": rule_id or "ALL",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════════
#  Rule Sync Gate MVP — 规则同步执行闸门
# ════════════════════════════════════════════════════════

@router.get("/rules/gate")
async def get_rule_sync_gate():
    """获取规则同步执行闸门状态。"""
    try:
        from backend.services.rebate_arb.rule_sync_gate import rule_sync_gate
        return rule_sync_gate.get_state()
    except Exception as e:
        return {"rebate_pause": False, "v3_pause": False, "error": str(e)}


@router.post("/rules/pause")
async def pause_rule_sync_gate(req: RuleGatePauseRequest):
    """暂停 Rebate/S1-S8；仅 v3_pause=true 时暂停 V3。"""
    try:
        from backend.services.rebate_arb.rule_sync_gate import rule_sync_gate
        return {
            "success": True,
            "gate": rule_sync_gate.pause(
                strategies=req.strategies,
                reason=req.reason,
                rebate_pause=req.rebate_pause,
                v3_pause=req.v3_pause,
                requires_code_change=req.requires_code_change,
                allow_manual_override=req.allow_manual_override,
                actor_user_id=req.actor_user_id,
                risk_acknowledged=req.risk_acknowledged,
            ),
        }
    except Exception as e:
        logger.error("[RebateRoutes] pause_rule_sync_gate error: %s", e, exc_info=True)
        return {"success": False, "error": str(e)}


@router.post("/rules/resume")
async def resume_rule_sync_gate(req: RuleGateResumeRequest):
    """解除规则同步闸门暂停。"""
    try:
        from backend.services.rebate_arb.rule_sync_gate import rule_sync_gate
        return {
            "success": True,
            "gate": rule_sync_gate.resume(
                reason=req.reason,
                actor_user_id=req.actor_user_id,
                risk_acknowledged=req.risk_acknowledged,
            ),
        }
    except Exception as e:
        logger.error("[RebateRoutes] resume_rule_sync_gate error: %s", e, exc_info=True)
        return {"success": False, "error": str(e)}


@router.get("/rules/sources")
async def get_rule_sources():
    """六所规则源列表：全开监控，分批自动暂停。"""
    try:
        from backend.services.rebate_arb.rule_registry import rule_registry
        return {"sources": rule_registry.list_sources()}
    except Exception as e:
        return {"sources": [], "error": str(e)}


@router.get("/rules/strategy-params")
async def get_rule_strategy_params():
    """查看由 RuleRegistry 管理的策略规则参数。"""
    try:
        from backend.services.rebate_arb.rule_registry import rule_registry
        return {"strategies": rule_registry.list_strategy_rule_params()}
    except Exception as e:
        return {"strategies": {}, "error": str(e)}


@router.get("/rules/scheduler")
async def get_rule_sync_scheduler_status():
    """规则同步后台调度状态。"""
    try:
        from backend.services.rebate_arb.rule_sync_scheduler import (
            RULE_SYNC_JOB_ID,
            get_rule_sync_interval_seconds,
            is_rule_sync_enabled,
        )
        from backend.services.scheduler import task_scheduler

        job = None
        if task_scheduler.scheduler:
            job = task_scheduler.scheduler.get_job(RULE_SYNC_JOB_ID)
        return {
            "enabled": is_rule_sync_enabled(),
            "job_id": RULE_SYNC_JOB_ID,
            "interval_seconds": get_rule_sync_interval_seconds(),
            "registered": bool(job),
            "next_run_time": str(job.next_run_time) if job else None,
        }
    except Exception as e:
        return {"enabled": False, "registered": False, "error": str(e)}


@router.post("/rules/ingest")
async def ingest_rule_snapshot(req: RuleSnapshotIngestRequest):
    """手动/调度采集规则文本快照，并自动生成 diff/分析/暂停。"""
    try:
        from backend.services.rebate_arb.rule_change_detector import rule_change_detector
        return rule_change_detector.ingest_snapshot(
            source_id=req.source_id,
            content_text=req.content_text,
            title=req.title,
            url=req.url,
        )
    except Exception as e:
        logger.error("[RebateRoutes] ingest_rule_snapshot error: %s", e, exc_info=True)
        return {"success": False, "error": str(e)}


@router.post("/rules/fetch/{source_id}")
async def fetch_rule_source(source_id: str):
    """抓取单个规则源 URL 并生成快照。"""
    try:
        from backend.services.rebate_arb.rule_change_detector import rule_change_detector
        return rule_change_detector.fetch_source(source_id)
    except Exception as e:
        logger.error("[RebateRoutes] fetch_rule_source error: %s", e, exc_info=True)
        return {"success": False, "error": str(e)}


@router.post("/rules/fetch-all")
async def fetch_all_rule_sources():
    """顺序抓取六所全部注册规则源。"""
    try:
        from backend.services.rebate_arb.rule_change_detector import rule_change_detector
        return rule_change_detector.fetch_all_sources()
    except Exception as e:
        logger.error("[RebateRoutes] fetch_all_rule_sources error: %s", e, exc_info=True)
        return {"success": False, "error": str(e)}


@router.get("/rules/changes")
async def get_rule_changes(status: str = "", limit: int = Query(100, ge=1, le=500)):
    """规则变更事件队列。"""
    try:
        from backend.services.rebate_arb.rule_change_detector import rule_change_detector
        return rule_change_detector.list_events(status=status, limit=limit)
    except Exception as e:
        return {"count": 0, "events": [], "error": str(e)}


@router.post("/rules/changes/{event_id}/analyze")
async def analyze_rule_change(event_id: int):
    """重新分析规则变更事件。"""
    try:
        from backend.services.rebate_arb.rule_change_detector import rule_change_detector
        return rule_change_detector.analyze_event(event_id)
    except Exception as e:
        logger.error("[RebateRoutes] analyze_rule_change error: %s", e, exc_info=True)
        return {"success": False, "error": str(e)}


@router.patch("/rules/changes/{event_id}")
async def mark_rule_change(event_id: int, req: RuleStatusPatch):
    """更新规则变更状态：pending/analyzed/applied/dismissed。"""
    try:
        from backend.services.rebate_arb.rule_change_detector import rule_change_detector
        return rule_change_detector.mark_event(event_id, req.status)
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.patch("/evolution/proposals/{proposal_id}")
async def mark_evolution_proposal(proposal_id: int, req: RuleStatusPatch):
    """更新进化/规则提案状态。"""
    try:
        from backend.services.rebate_arb.rule_change_detector import rule_change_detector
        return rule_change_detector.mark_proposal(proposal_id, req.status)
    except Exception as e:
        return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════════
#  GET /reconcile — 触发仓位对账
# ════════════════════════════════════════════════════════

@router.get("/reconcile")
async def run_reconciliation():
    """触发仓位对账（内存 vs DB vs 交易所）"""
    try:
        from backend.services.rebate_arb.engine import rebate_arb_engine
        from backend.services.rebate_arb.position_reconciler import position_reconciler

        report = position_reconciler.reconcile(rebate_arb_engine)
        return report.to_dict()
    except Exception as e:
        return {"is_consistent": False, "error": str(e)}


# ════════════════════════════════════════════════════════
#  PATCH /mode — 切换 paper/live 模式
# ════════════════════════════════════════════════════════

@router.patch("/mode")
async def switch_mode(req: SwitchModeRequest):
    """切换引擎 paper/live 模式"""
    try:
        from backend.services.rebate_arb.engine import rebate_arb_engine

        if req.mode not in ("paper", "live"):
            return {"success": False, "error": "mode must be 'paper' or 'live'"}

        # set_paper_account 同时设置 engine._paper_mode 和 capital_coordinator
        rebate_arb_engine.set_paper_account(
            req.paper_account_id if req.mode == "paper" else None
        )
        return {
            "success": True,
            "mode": req.mode,
            "paper_account_id": req.paper_account_id if req.mode == "paper" else None,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════════
#  GET /config — 当前配置
# ════════════════════════════════════════════════════════

@router.get("/config")
async def get_rebate_config():
    """获取当前返利套利引擎配置"""
    try:
        from backend.config.rebate_config_loader import rebate_config
        from backend.services.rebate_arb.engine import rebate_arb_engine
        from backend.services.rebate_arb.capital_coordinator import capital_coordinator

        if not rebate_config:
            return {"loaded": False}

        return {
            "loaded": True,
            "engine": {
                "min_monthly_value": rebate_config.engine.min_monthly_value,
                "max_position_usd": rebate_config.engine.max_position_usd,
                "max_total_volume_7d": rebate_config.engine.max_total_volume_7d,
                "max_holding_days": rebate_config.engine.max_holding_days,
                "paper_mode": rebate_config.engine.paper_mode,
            },
            "risk_gate": {
                "max_daily_volume_per_exchange": rebate_config.risk_gate.max_daily_volume_per_exchange,
                "max_weekly_volume_per_exchange": rebate_config.risk_gate.max_weekly_volume_per_exchange,
                "max_daily_loss_pct": rebate_config.risk_gate.daily_loss_circuit_breaker_pct,
            },
            "exchanges_enabled": [
                name for name, cfg in rebate_config.exchanges.items()
                if cfg.enabled
            ],
            "strategies_enabled": [
                name for name, cfg in rebate_config.strategies.items()
                if cfg.enabled
            ],
            "current_mode": "paper" if rebate_arb_engine.paper_mode else "live",
            "paper_account_id": capital_coordinator.get_paper_account_id() if capital_coordinator.is_paper_mode() else None,
        }
    except Exception as e:
        return {"loaded": False, "error": str(e)}


# ════════════════════════════════════════════════════════
#  GET /config/strategies — 获取所有策略配置
# ════════════════════════════════════════════════════════

@router.get("/config/strategies")
async def get_strategy_configs():
    """获取 S1-S8 策略配置（参数 + 启用状态 + 风控覆盖）"""
    try:
        from backend.services.rebate_arb.strategies import ALL_STRATEGIES
        from backend.services.rebate_arb.risk_gate import rebate_risk_gate
        from backend.config.rebate_config_loader import rebate_config

        overrides = rebate_risk_gate.get_strategy_overrides()
        strategies = {}
        for sid, strategy in ALL_STRATEGIES.items():
            # Extract tunable params (class-level uppercase attributes)
            params = {}
            for attr in dir(strategy):
                if attr.isupper() and not attr.startswith("_"):
                    val = getattr(strategy, attr)
                    if isinstance(val, (int, float, str, bool)):
                        params[attr] = val

            # Get enabled status from config
            enabled = True
            if rebate_config and hasattr(rebate_config, "strategies"):
                for key, item in rebate_config.strategies.items():
                    if key.startswith(sid):
                        enabled = item.enabled
                        break

            strategies[sid] = {
                "enabled": enabled,
                "params": params,
                "risk_overrides": overrides.get(sid, {}),
            }

        return {"strategies": strategies}
    except Exception as e:
        logger.error(f"[RebateRoutes] get_strategy_configs error: {e}", exc_info=True)
        return {"strategies": {}, "error": str(e)}


# ════════════════════════════════════════════════════════
#  PATCH /config/strategies/{strategy_id} — 更新策略配置
# ════════════════════════════════════════════════════════

@router.patch("/config/strategies/{strategy_id}")
async def patch_strategy_config(strategy_id: str, body: StrategyConfigPatch):
    """运行时更新单个策略的参数/风控覆盖"""
    try:
        from backend.services.rebate_arb.engine import rebate_arb_engine

        patch = {"strategies": {strategy_id: {}}}
        if body.params:
            patch["strategies"][strategy_id]["params"] = body.params
        if body.risk_overrides:
            patch["strategies"][strategy_id]["risk_overrides"] = body.risk_overrides
        if body.enabled is not None:
            patch["strategies"][strategy_id]["enabled"] = body.enabled

        changes = rebate_arb_engine.apply_config_patch(patch)
        return {"success": True, "strategy_id": strategy_id, "changes": changes}
    except Exception as e:
        logger.error(f"[RebateRoutes] patch_strategy_config error: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════════
#  PATCH /config/engine — 更新引擎全局参数
# ════════════════════════════════════════════════════════

@router.patch("/config/engine")
async def patch_engine_config(body: EngineConfigPatch):
    """运行时更新引擎全局参数"""
    try:
        from backend.services.rebate_arb.engine import rebate_arb_engine

        patch = {"engine": body.model_dump(exclude_none=True)}
        changes = rebate_arb_engine.apply_config_patch(patch)
        return {"success": True, "changes": changes}
    except Exception as e:
        logger.error(f"[RebateRoutes] patch_engine_config error: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════════
#  PATCH /config/risk-gate — 更新风控全局阈值
# ════════════════════════════════════════════════════════

@router.patch("/config/risk-gate")
async def patch_risk_gate_config(body: RiskGateConfigPatch):
    """运行时更新风控全局阈值"""
    try:
        from backend.services.rebate_arb.engine import rebate_arb_engine

        patch = {"risk_gate": body.model_dump(exclude_none=True)}
        changes = rebate_arb_engine.apply_config_patch(patch)
        return {"success": True, "changes": changes}
    except Exception as e:
        logger.error(f"[RebateRoutes] patch_risk_gate_config error: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════════
#  GET /events — 获取事件日志
# ════════════════════════════════════════════════════════

@router.get("/events")
async def get_events(since: float = Query(0.0), limit: int = Query(50, ge=1, le=200)):
    """获取引擎事件日志（since 为 unix timestamp）"""
    try:
        from backend.services.rebate_arb.engine import rebate_arb_engine

        events = rebate_arb_engine.get_events(since=since, limit=limit)
        latest_ts = events[-1]["ts"] if events else since
        return {"events": events, "latest_ts": latest_ts}
    except Exception as e:
        return {"events": [], "latest_ts": 0.0, "error": str(e)}


# ════════════════════════════════════════════════════════
#  GET /points/summary — 积分汇总
# ════════════════════════════════════════════════════════

@router.get("/points/summary")
async def get_points_summary():
    """各交易所积分汇总（余额、乘数、估值、转换收益）"""
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import RebatePositionDB, RebatePerformanceLogDB
        from backend.services.rebate_arb.points_aggregation import (
            aggregate_points_and_pnl,
            build_exchange_points_payload,
            point_usd_rate,
            points_to_usd,
        )

        db = SessionLocal()
        try:
            active = db.query(RebatePositionDB).filter(
                RebatePositionDB.status.in_(["active", "holding"])
            ).all()
            all_positions = db.query(RebatePositionDB).all()
            logs = db.query(RebatePerformanceLogDB).all()

            exchange_stats, strategy_stats, total_points, total_pnl = aggregate_points_and_pnl(
                active,
                logs,
                pos_lookup=all_positions,
            )
            exchanges = build_exchange_points_payload(exchange_stats)

            by_strategy = {
                sid: {
                    "points_earned_total": round(float(stats["points_earned"]), 2),
                    "estimated_value_usd": round(points_to_usd(stats["points_earned"]), 4),
                    "conversion_revenue_usd": round(float(stats["pnl"]), 4),
                    "position_count": int(stats.get("position_count") or 0),
                }
                for sid, stats in strategy_stats.items()
            }

            return {
                "exchanges": exchanges,
                "by_strategy": by_strategy,
                "total_points_earned": round(total_points, 2),
                "total_estimated_value_usd": round(points_to_usd(total_points), 4),
                "total_conversion_revenue_usd": round(total_pnl, 4),
                "point_usd_rate": point_usd_rate(),
                "valuation_note": "Stage6 投机性估值（usd_per_point×discount），非官方兑换比例",
            }
        finally:
            db.close()
    except Exception as e:
        logger.error(f"[RebateRoutes] get_points_summary error: {e}", exc_info=True)
        return {"exchanges": {}, "total_points_earned": 0, "error": str(e)}


# ════════════════════════════════════════════════════════
#  GET /points/transactions — 积分交易明细
# ════════════════════════════════════════════════════════

@router.get("/points/transactions")
async def get_points_transactions(
    exchange: Optional[str] = Query(None),
    strategy: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """积分相关交易明细"""
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import RebatePerformanceLogDB

        db = SessionLocal()
        try:
            from backend.services.rebate_arb.points_aggregation import is_trade_performance_log

            query = db.query(RebatePerformanceLogDB)
            if strategy:
                query = query.filter(RebatePerformanceLogDB.strategy_type == strategy)
            query = query.order_by(RebatePerformanceLogDB.id.desc()).limit(limit * 3)
            logs = query.all()

            transactions = []
            for log in logs:
                if not is_trade_performance_log(log):
                    continue
                transactions.append({
                    "position_id": log.position_id,
                    "strategy_type": log.strategy_type,
                    "points": float(log.total_points or 0),
                    "pnl": float(log.total_pnl or 0),
                    "rebate": float(log.total_rebate or 0),
                    "hold_hours": float(log.hold_hours or 0),
                    "close_reason": log.close_reason,
                })
                if len(transactions) >= limit:
                    break

            return {"transactions": transactions, "count": len(transactions)}
        finally:
            db.close()
    except Exception as e:
        logger.error(f"[RebateRoutes] get_points_transactions error: {e}", exc_info=True)
        return {"transactions": [], "error": str(e)}


# ════════════════════════════════════════════════════════
#  Evolution MVP — 进化/学习/回测
# ════════════════════════════════════════════════════════

@router.get("/evolution/summary")
async def get_evolution_summary():
    """获取 Rebate 策略进化摘要。"""
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import RebatePerformanceLogDB, RebateTradeOutcomeDB

        db = SessionLocal()
        try:
            from backend.services.rebate_arb.points_aggregation import is_trade_performance_log

            perf_rows = (
                db.query(RebatePerformanceLogDB)
                .order_by(RebatePerformanceLogDB.id.desc())
                .limit(500)
                .all()
            )
            outcome_count = db.query(RebateTradeOutcomeDB).count()
            by_strategy: Dict[str, Dict[str, Any]] = {}
            trade_rows = [r for r in perf_rows if is_trade_performance_log(r)]
            for row in trade_rows:
                bucket = by_strategy.setdefault(row.strategy_type, {
                    "count": 0,
                    "pnl": 0.0,
                    "rebate": 0.0,
                    "points": 0.0,
                    "wins": 0,
                })
                net = float(row.total_pnl or 0) + float(row.total_rebate or 0)
                bucket["count"] += 1
                bucket["pnl"] += float(row.total_pnl or 0)
                bucket["rebate"] += float(row.total_rebate or 0)
                bucket["points"] += float(row.total_points or 0)
                bucket["wins"] += 1 if net > 0 else 0
            for bucket in by_strategy.values():
                bucket["win_rate"] = bucket["wins"] / bucket["count"] if bucket["count"] else 0
                bucket["net_value"] = bucket["pnl"] + bucket["rebate"]
        finally:
            db.close()
        return {
            "outcome_count": outcome_count,
            "sample_count": len(trade_rows),
            "by_strategy": by_strategy,
            "live_apply_requires_manual_confirm": True,
        }
    except Exception as e:
        return {"outcome_count": 0, "sample_count": 0, "by_strategy": {}, "error": str(e)}


@router.post("/evolution/backtest")
async def run_evolution_backtest(strategy_type: str = "S8"):
    """MVP 回测：基于近 500 条结算日志做规则提案评分。"""
    try:
        from backend.services.rebate_arb.rebate_backtest_runner import rebate_backtest_runner
        return rebate_backtest_runner.run(strategy_type)
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/evolution/generate")
async def generate_evolution_proposals():
    """根据历史 outcome 生成待人工确认的进化提案。"""
    try:
        from backend.services.rebate_arb.rebate_strategy_evolver import rebate_strategy_evolver
        return rebate_strategy_evolver.generate_proposals()
    except Exception as e:
        return {"success": False, "error": str(e), "count": 0, "proposals": []}


@router.get("/evolution/proposals")
async def get_evolution_proposals():
    """生成待人工确认的策略进化提案。"""
    import json
    summary = await get_evolution_summary()
    proposals = []
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import RebateEvolutionProposalDB

        db = SessionLocal()
        try:
            rows = (
                db.query(RebateEvolutionProposalDB)
                .filter(RebateEvolutionProposalDB.status == "pending")
                .order_by(RebateEvolutionProposalDB.id.desc())
                .limit(100)
                .all()
            )
            for row in rows:
                try:
                    change = json.loads(row.proposal_json or "{}")
                except Exception:
                    change = {}
                proposals.append({
                    "id": row.id,
                    "source": row.source,
                    "strategy_type": row.strategy_type,
                    "severity": row.severity,
                    "title": row.title,
                    "change": change,
                    "requires_manual_live_confirm": row.requires_manual_live_confirm,
                    "related_event_id": row.related_event_id,
                })
        finally:
            db.close()
    except Exception as e:
        logger.debug("[RebateRoutes] db proposals skipped: %s", e)

    for sid, stats in summary.get("by_strategy", {}).items():
        if stats.get("count", 0) < 3:
            continue
        if stats.get("win_rate", 0) < 0.45:
            proposals.append({
                "strategy_type": sid,
                "severity": "medium",
                "title": f"{sid} 胜率偏低，建议 Paper 降仓验证",
                "change": {"max_position_multiplier": 0.8},
                "requires_manual_live_confirm": True,
            })
        elif stats.get("win_rate", 0) > 0.65 and stats.get("net_value", 0) > 0:
            proposals.append({
                "strategy_type": sid,
                "severity": "low",
                "title": f"{sid} 表现较好，可在 Paper 中小幅加仓",
                "change": {"max_position_multiplier": 1.1},
                "requires_manual_live_confirm": True,
            })
    return {"count": len(proposals), "proposals": proposals, "summary": summary}


def _get_cached_incentive_data() -> Dict[str, Any]:
    """获取缓存的激励数据（尝试从 ExchangeManager 获取真实数据）"""
    try:
        from backend.services.exchange.exchange_manager import get_exchange_manager
        manager = get_exchange_manager()
        if not manager:
            return {}

        data = {}
        clients = manager.get_all_clients()
        for exchange_name, client in clients.items():
            try:
                fee_tier = client.get_fee_tier()
                rebate_info = client.get_rebate_info()
                data[exchange_name] = {
                    "maker_rate": fee_tier.maker_rate,
                    "taker_rate": fee_tier.taker_rate,
                    "rebate_rate": fee_tier.rebate_rate,
                    "current_rebate_rate": rebate_info.current_rebate_rate,
                }
            except Exception:
                pass
        return data
    except Exception:
        return {}


def _get_cached_funding_rates() -> Dict[str, float]:
    """获取缓存的资金费率数据"""
    try:
        from backend.services.arbitrage.opportunity_scanner import opportunity_scanner
        # 尝试从已有缓存获取
        opps = opportunity_scanner.get_active_opportunities()
        rates = {}
        for o in opps:
            if o.funding_snapshot:
                rates[o.symbol] = o.funding_snapshot.current_rate
        return rates
    except Exception:
        return {}


def _get_account_equity() -> float:
    """获取账户权益"""
    try:
        from backend.services.exchange.exchange_manager import get_exchange_manager
        manager = get_exchange_manager()
        if not manager:
            return 0.0

        total = 0.0
        clients = manager.get_all_clients()
        for client in clients.values():
            try:
                balance = client.get_balance()
                total += balance.total_equity
            except Exception:
                pass
        return total
    except Exception:
        return 0.0


# ════════════════════════════════════════════════════════
#  POST /config/ai-generate — AI 一键配置生成
# ════════════════════════════════════════════════════════

@router.post("/config/ai-generate")
async def ai_generate_config(request: AiConfigGenerateRequest):
    """通过 LLM 或 fallback 模板生成最优配置（不自动应用，需前端确认）"""
    try:
        from backend.services.rebate_arb.ai_config_generator import generate_ai_config

        result = await generate_ai_config(
            risk_profile=request.risk_profile,
            total_equity=request.total_equity,
            target_exchanges=request.target_exchanges,
            goal=request.goal,
        )
        return result
    except Exception as e:
        logger.error(f"[RebateAPI] AI config generation failed: {e}")
        return {
            "success": False,
            "source": "error",
            "config": None,
            "reasoning": "",
            "error": str(e),
        }
