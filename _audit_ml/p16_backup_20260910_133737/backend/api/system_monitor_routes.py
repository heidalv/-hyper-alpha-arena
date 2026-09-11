"""
系统监控 API — 暴露后端新模块给前端

Endpoints:
  GET  /api/monitor/data-quality          — 数据质量报告 + 告警
  GET  /api/monitor/factor-eval/{symbol}  — 因子 IC 质量评估
  GET  /api/monitor/hypothesis            — 策略假设引擎状态
  POST /api/monitor/hypothesis/run        — 手动触发假设生成
  GET  /api/monitor/fee-profile           — 交易所费率档案
  GET  /api/monitor/fee-report            — 手续费效率分析
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/monitor", tags=["System Monitor"])


# ═══════════ Thread Diagnostics（2026-07-11 新增，排查线程数只增不减问题）═══════════

@router.get("/thread-stats")
def get_thread_stats():
    """当前进程 Python 线程快照：按名字前缀分组计数 + 存活时间最长的几个。

    用途：诊断"数据库连接越来越慢/卡死"问题时，用来确认是否有某类后台线程在
    持续堆积而不退出（正常应该是几十个固定的常驻线程，数量长期稳定）。
    """
    import re
    import threading

    threads = threading.enumerate()
    by_prefix: dict = {}
    details = []
    for t in threads:
        name = t.name or "unnamed"
        # 去掉 ThreadPoolExecutor 自动编号后缀（如 "_0", "-3_2"）方便按类聚合
        prefix = re.sub(r"[_-]?\d+(_\d+)?$", "", name) or name
        by_prefix[prefix] = by_prefix.get(prefix, 0) + 1
        details.append({
            "name": name,
            "daemon": t.daemon,
            "alive": t.is_alive(),
            "ident": t.ident,
        })
    return {
        "total_threads": len(threads),
        "by_prefix": dict(sorted(by_prefix.items(), key=lambda kv: -kv[1])),
        "sample": details[:200],
    }


# ═══════════ Data Quality ═══════════

@router.get("/data-quality")
def get_data_quality():
    """数据质量综合报告: 数据源健康 + 近期告警 + K线新鲜度 + 三链路健康。"""
    try:
        from services.data_quality_monitor import get_data_quality_monitor
        dq = get_data_quality_monitor()
        # [v6 2.3] 链路缺口检测（行情/K线/链上 last_success 缺口告警）
        try:
            dq.check_link_gaps()
        except Exception as e:
            logger.debug(f"[Monitor] link_gap check failed: {e}")
        # K线新鲜度巡检状态（已由 kline_freshness_inspector 定期填充）
        freshness = {}
        try:
            from backend.services.kline_freshness_inspector import kline_freshness_inspector
            freshness = kline_freshness_inspector.status()
        except Exception as e:
            logger.debug(f"[Monitor] kline_freshness status failed: {e}")
        # [v6 2.3] 三链路健康视图 + DataProvider tier 状态
        link_health = {}
        try:
            link_health = dq.get_link_health()
        except Exception as e:
            logger.debug(f"[Monitor] link_health failed: {e}")
        return {
            "source_health": dq.get_source_health_report(),
            "recent_alerts": dq.get_recent_alerts(limit=80),
            "stale_threshold_sec": dq.STALE_THRESHOLD_SEC,
            "kline_freshness": freshness,
            "link_health": link_health,
        }
    except Exception as e:
        logger.error(f"[Monitor] data-quality error: {e}")
        return {"source_health": {}, "recent_alerts": [], "error": str(e)}


@router.post("/data-quality/kline-check")
def trigger_kline_freshness_check():
    """手动触发一次 K线新鲜度检测（不等定时巡检）。"""
    try:
        from backend.services.kline_freshness_inspector import kline_freshness_inspector
        return kline_freshness_inspector.check_once()
    except Exception as e:
        logger.error(f"[Monitor] kline-check error: {e}")
        return {"alerts": [], "error": str(e)}

@router.get("/factor-eval/{symbol}")
def get_factor_evaluation(
    symbol: str,
    top_n: int = Query(default=20, ge=1, le=100),
):
    """
    对指定 symbol 的因子做 IC 质量评估（需要该 symbol 的 K线和因子数据）。
    """
    try:
        from services.factor_engine.factor_evaluator import FactorEvaluator
        import pandas as pd

        evaluator = FactorEvaluator(forward_period=5)

        klines = _load_klines_for_eval(symbol)
        if klines is None or len(klines) < 60:
            return {"symbol": symbol, "reports": [], "message": "Insufficient kline data"}

        close = klines["close"]

        factor_cols = [c for c in klines.columns if c not in (
            "open", "high", "low", "close", "volume", "timestamp",
            "open_time", "close_time", "date",
        )]

        reports = []
        for col in factor_cols[:top_n * 2]:
            fv = klines.get(col)
            if fv is None or fv.dropna().empty:
                continue
            try:
                r = evaluator.evaluate_factor(col, fv, close)
                reports.append({
                    "factor_id": r.factor_id,
                    "ic_mean": round(r.ic_mean, 5),
                    "ic_std": round(r.ic_std, 5),
                    "icir": round(r.icir, 3),
                    "ic_positive_pct": round(r.ic_positive_pct, 3),
                    "ic_decay_halflife": r.ic_decay_halflife,
                    "turnover": round(r.turnover, 4),
                    "monotonicity": round(r.monotonicity, 4),
                    "tail_risk": round(r.tail_risk, 5),
                    "grade": r.grade,
                    "data_points": r.data_points,
                })
            except Exception:
                pass

        reports.sort(key=lambda x: abs(x["ic_mean"]), reverse=True)
        return {"symbol": symbol, "reports": reports[:top_n]}

    except Exception as e:
        logger.error(f"[Monitor] factor-eval error: {e}")
        return {"symbol": symbol, "reports": [], "error": str(e)}


def _load_klines_for_eval(symbol: str):
    """尝试从 unified_data_pool 获取已增强的 DataFrame。"""
    try:
        from services.unified_data_pool import UnifiedDataPool
        pool = UnifiedDataPool.__new__(UnifiedDataPool)
        pool.__init__()
        data = pool.get_enriched_klines(symbol, limit=500)
        if data is not None and len(data) > 0:
            return data
    except Exception:
        pass

    try:
        from services.kline_service import get_kline_service
        ks = get_kline_service()
        return ks.get_klines_df(symbol, period="1h", limit=500)
    except Exception:
        pass

    return None


# ═══════════ Learning Loop Health ═══════════

@router.get("/learning-loop")
def get_learning_loop_health():
    """学习闭环健康状态：paper 平仓是否被同步进 strategy_trades。"""
    try:
        from sqlalchemy import cast
        from sqlalchemy.types import Text
        from backend.database.connection import SessionLocal
        from backend.database.models import PaperPosition, StrategyTrade
        from backend.services.learning_loop_service import learning_loop

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        db = SessionLocal()
        try:
            closed_positions = (
                db.query(PaperPosition)
                .filter(
                    PaperPosition.status == "closed",
                    PaperPosition.closed_at.isnot(None),
                    PaperPosition.closed_at >= cutoff.replace(tzinfo=None),
                )
                .all()
            )
            synced = 0
            unsynced = []
            for pos in closed_positions:
                found = (
                    db.query(StrategyTrade)
                    .filter(
                        cast(StrategyTrade.decision_context, Text).contains(
                            f'"paper_position_id": {pos.id}'
                        )
                    )
                    .first()
                )
                if found:
                    synced += 1
                else:
                    unsynced.append({
                        "paper_position_id": pos.id,
                        "strategy_id": pos.strategy_id,
                        "symbol": pos.symbol,
                        "side": pos.side,
                        "closed_at": pos.closed_at.isoformat() if pos.closed_at else None,
                    })
        finally:
            db.close()

        metrics = learning_loop.metrics()
        return {
            "window_hours": 24,
            "closed_paper_positions": len(closed_positions),
            "synced_strategy_trades": synced,
            "unsynced_count": len(unsynced),
            "unsynced_recent": unsynced[:20],
            "learning_loop": learning_loop.status(),
            "metrics": metrics,
            "alert": len(closed_positions) > 0 and synced == 0,
        }
    except Exception as e:
        logger.error(f"[Monitor] learning-loop error: {e}")
        return {"error": str(e)}


# ═══════════ Hypothesis Engine ═══════════

@router.get("/hypothesis")
def get_hypothesis_status():
    """策略假设引擎状态: 从数据库读取真实生成/验证/晋升记录。"""
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import StrategyHypothesis
        from backend.services.strategy_hypothesis_engine import StrategyHypothesisEngine

        engine = StrategyHypothesisEngine()
        db = SessionLocal()
        try:
            rows = (
                db.query(StrategyHypothesis)
                .order_by(StrategyHypothesis.created_at.desc())
                .limit(100)
                .all()
            )
        finally:
            db.close()

        generated = []
        validated = []
        promoted = 0
        for row in rows:
            meta = row.param_ranges or {}
            status = meta.get("status") or ("promoted" if row.promoted else "generated")
            if status == "promoted" or row.promoted:
                promoted += 1
            item = {
                "id": row.hypothesis_id,
                "description": row.description or "",
                "symbol": meta.get("symbol", ""),
                "exchange": meta.get("exchange", ""),
                "period": meta.get("period", ""),
                "direction": meta.get("direction", "neutral"),
                "confidence": float(meta.get("confidence") or 0),
                "regime": row.market_regime or "",
                "source": meta.get("source", ""),
                "status": status,
                "snapshot_id": meta.get("snapshot_id", ""),
                "data_source": meta.get("data_source", ""),
                "data_quality": meta.get("data_quality", {}),
                "qaa_correlation_id": meta.get("qaa_correlation_id", ""),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            generated.append(item)
            validation = meta.get("validation_result") or {}
            if validation:
                validated.append({
                    "id": row.hypothesis_id,
                    "passed": bool(validation.get("passed")),
                    "sharpe": round(float(validation.get("sharpe") or 0), 3),
                    "win_rate": round(float(validation.get("win_rate") or 0), 3),
                    "max_dd": round(float(validation.get("max_drawdown_pct") or 0), 2),
                    "trades": int(validation.get("total_trades") or 0),
                    "pnl": round(float(validation.get("total_pnl") or 0), 2),
                    "error": validation.get("error", ""),
                    "status": status,
                    "exchange": meta.get("exchange", ""),
                    "symbol": meta.get("symbol", ""),
                    "period": meta.get("period", ""),
                    "snapshot_id": meta.get("snapshot_id", ""),
                    "data_source": meta.get("data_source", ""),
                    "promoted_template_id": validation.get("promoted_template_id") or meta.get("promoted_template_id", ""),
                })

        return {
            "total_generated": len(rows),
            "total_validated": len(validated),
            "total_promoted": promoted,
            "thresholds": {
                "min_sharpe": engine.MIN_SHARPE,
                "min_win_rate": engine.MIN_WIN_RATE,
                "max_drawdown": engine.MAX_DRAWDOWN,
            },
            "recent_generated": generated,
            "recent_validated": validated,
        }
    except Exception as e:
        logger.error(f"[Monitor] hypothesis error: {e}")
        return {
            "total_generated": 0, "total_validated": 0, "total_promoted": 0,
            "recent_generated": [], "recent_validated": [],
            "error": str(e),
        }


class HypothesisRunRequest(BaseModel):
    symbols: list[str] = ["BTC"]
    exchange: Optional[str] = None
    market_type: str = "perp"
    period: str = "1h"


@router.post("/hypothesis/run")
def trigger_hypothesis_run(req: HypothesisRunRequest):
    """手动触发一次假设生成+验证。"""
    try:
        from backend.database.connection import SessionLocal
        from backend.services.strategy_hypothesis_engine import get_hypothesis_engine

        engine = get_hypothesis_engine()
        db = SessionLocal()
        try:
            context = {
                "regime": "unknown",
                "source": "manual_trigger",
                "exchange": req.exchange,
                "market_type": req.market_type,
                "period": req.period,
            }
            summary = engine.run_full_cycle(context, req.symbols, db=db)
        finally:
            db.close()

        results = []
        for item in summary.get("details", []):
            results.append({
                "hypothesis": item.get("id", ""),
                "passed": item.get("passed", False),
                "sharpe": round(float(item.get("sharpe") or 0), 3),
                "win_rate": round(float(item.get("win_rate") or 0), 3),
                "max_dd": round(float(item.get("max_dd") or 0), 2),
                "promoted_template_id": item.get("promoted_template_id", ""),
                "error": item.get("error", ""),
            })

        return {"triggered": summary.get("generated", 0), "results": results, "summary": summary}
    except Exception as e:
        logger.error(f"[Monitor] hypothesis run error: {e}")
        return {"triggered": 0, "results": [], "error": str(e)}


# ═══════════ Fee / Incentive ═══════════

@router.get("/fee-profile")
def get_fee_profile(
    exchange: str = Query(default="asterdex"),
    volume_30d: float = Query(default=0.0),
):
    """获取交易所费率档案。"""
    try:
        from services.exchange_incentive_monitor import ExchangeIncentiveMonitor
        mon = ExchangeIncentiveMonitor()
        p = mon.get_fee_profile(exchange, volume_30d)
        return {
            "exchange": p.exchange,
            "tier": p.tier,
            "maker_rate": p.maker_rate,
            "taker_rate": p.taker_rate,
            "volume_30d_usd": p.volume_30d_usd,
            "next_tier_threshold": p.next_tier_threshold,
            "savings_at_next_tier": p.savings_at_next_tier,
        }
    except Exception as e:
        logger.error(f"[Monitor] fee-profile error: {e}")
        return {"error": str(e)}


@router.get("/fee-report")
def get_fee_report(
    account_id: str = Query(default=""),
    days: int = Query(default=30, ge=1, le=365),
):
    """手续费效率分析报告。"""
    try:
        from services.exchange_incentive_monitor import ExchangeIncentiveMonitor
        from backend.database.connection import SessionLocal
        mon = ExchangeIncentiveMonitor()

        db = SessionLocal()
        try:
            r = mon.analyze_fee_efficiency(db=db, account_id=account_id, days=days)
        finally:
            db.close()

        tips = mon.get_optimization_tips()

        return {
            "period_days": r.period_days,
            "total_fee_usd": round(r.total_fee_usd, 4),
            "total_volume_usd": round(r.total_volume_usd, 2),
            "maker_pct": round(r.maker_pct, 3),
            "taker_pct": round(r.taker_pct, 3),
            "avg_fee_rate": round(r.avg_fee_rate, 6),
            "potential_savings_usd": round(r.potential_savings_usd, 4),
            "recommendations": r.recommendations,
            "optimization_tips": tips,
        }
    except Exception as e:
        logger.error(f"[Monitor] fee-report error: {e}")
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════
# Agent Runtime Monitor — 前端 AgentMonitorPanel 专用
# ═══════════════════════════════════════════════════════════════

@router.get("/agents/overview")
def get_agent_overview():
    """Agent 运行时状态总览(9个 Agent)。"""
    try:
        from backend.services.agent_runtime_monitor import agent_runtime_monitor
        return agent_runtime_monitor.get_overview()
    except Exception as e:
        logger.error(f"[Monitor] agents/overview error: {e}")
        return []


@router.get("/agents/frequency")
def get_agent_frequency(hours: int = Query(default=24, ge=1, le=168)):
    """Agent 调用频率统计。"""
    try:
        from backend.services.agent_runtime_monitor import agent_runtime_monitor
        return agent_runtime_monitor.get_frequency_stats(hours=hours)
    except Exception as e:
        logger.error(f"[Monitor] agents/frequency error: {e}")
        return {"agents": [], "hours": hours}


@router.get("/agents/logs")
def get_agent_logs(limit: int = Query(default=200, ge=1, le=1000)):
    """Agent 运行日志。"""
    try:
        from backend.services.agent_runtime_monitor import agent_runtime_monitor
        return agent_runtime_monitor.get_logs(limit=limit)
    except Exception as e:
        logger.error(f"[Monitor] agents/logs error: {e}")
        return []


@router.get("/agents/{agent_id}")
def get_agent_detail_api(agent_id: str):
    """单个 Agent 的详细运行状态。"""
    try:
        from backend.services.agent_runtime_monitor import agent_runtime_monitor
        detail = agent_runtime_monitor.get_agent_detail(agent_id)
        if detail is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
        return detail
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Monitor] agents/{agent_id} error: {e}")
        return {"error": str(e)}


@router.post("/agents/{agent_id}/reset")
def reset_agent_stats(agent_id: str):
    """重置 Agent 统计数据。"""
    try:
        from backend.services.agent_runtime_monitor import agent_runtime_monitor
        ok = agent_runtime_monitor.reset_agent(agent_id)
        return {"ok": ok, "agent_id": agent_id}
    except Exception as e:
        logger.error(f"[Monitor] agents/{agent_id}/reset error: {e}")
        return {"ok": False, "error": str(e)}


# ═══════════ 数据中台 / 四所 K 线同步门禁（阶段4）═══════════

@router.get("/kline-sync/status")
def get_kline_sync_status():
    """四所 catalog 覆盖 + P0/P1/P2 心跳 + 启动门禁摘要。"""
    try:
        from backend.services.data_center_gate import run_startup_gate
        from backend.services.data_center import data_center
        gate = run_startup_gate(block_on_hard_fail=False)
        return {
            "gate": gate,
            "heartbeats": data_center.get_sync_heartbeats(),
            "coverage": data_center.get_catalog_coverage(),
            "active_symbols_sample": data_center.list_symbols()[:20],
        }
    except Exception as e:
        logger.error(f"[Monitor] kline-sync/status error: {e}")
        return {"error": str(e)}


@router.get("/kline-sync/gate")
def get_kline_sync_gate():
    """仅返回门禁检查结果（CI / 运维探针）。"""
    try:
        from backend.services.data_center_gate import run_startup_gate
        return run_startup_gate(block_on_hard_fail=False)
    except Exception as e:
        logger.error(f"[Monitor] kline-sync/gate error: {e}")
        return {"ok": False, "error": str(e)}
