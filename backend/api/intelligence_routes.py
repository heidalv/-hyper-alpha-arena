"""
智能情报中心 API — 新闻 / 鲸鱼 / 合约 / 情绪 / 多周期 / 复盘

为前端提供所有情报数据的统一入口
"""
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.database.connection import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/intelligence", tags=["intelligence"])


# ═══════════════════ 情绪指数 ═══════════════════

@router.get("/sentiment/{symbol}")
def get_sentiment(symbol: str = "BTC"):
    """获取综合情绪指数"""
    from backend.services.sentiment_composite_service import sentiment_composite
    result = sentiment_composite.calculate(symbol)
    return {
        "symbol": symbol,
        "index": result.index,
        "zone": result.zone,
        "guidance": result.trading_guidance,
        "factors": result.factors,
        "timestamp": result.timestamp,
    }


# ═══════════════════ 新闻情报 ═══════════════════

@router.get("/news/{symbol}")
def get_news(symbol: str = "BTC", hours: int = Query(default=24, le=168)):
    """获取最近新闻信号"""
    from backend.services.news_intelligence_service import news_intelligence
    signals = news_intelligence.get_recent_signals(symbol, hours)
    aggregate = news_intelligence.get_aggregate_sentiment(symbol, hours)
    return {
        "symbol": symbol,
        "aggregate_sentiment": aggregate,
        "events": signals,
    }


@router.post("/news/fetch")
async def trigger_news_fetch(db: Session = Depends(get_db)):
    """手动触发新闻拉取和分析"""
    from backend.services.news_intelligence_service import news_intelligence
    results = await news_intelligence.fetch_and_analyze(db)
    return {"fetched": len(results), "events": results}


# ═══════════════════ 鲸鱼追踪 ═══════════════════

@router.get("/whale/{symbol}")
def get_whale(symbol: str = "BTC", hours: int = Query(default=4, le=48)):
    """获取鲸鱼异动"""
    from backend.services.whale_tracker_service import whale_tracker
    signal = whale_tracker.get_whale_signal(symbol)
    activities = whale_tracker.get_recent_activities(symbol, hours)

    # DB 为空时，从实时数据补充展示
    if not activities:
        try:
            txs = whale_tracker._fetch_whale_transactions()
            for tx in txs[:8]:
                interp = whale_tracker._heuristic_interpret(tx)
                activities.append({
                    "id": 0,
                    "type": interp.get("activity_type", "transfer"),
                    "symbol": tx.get("symbol", "BTC"),
                    "direction": "buy" if interp.get("signal_direction", 0) > 0 else "sell",
                    "amount_usd": tx.get("amount_usd", 0),
                    "from": tx.get("from_owner", ""),
                    "to": tx.get("to_owner", ""),
                    "signal_direction": interp.get("signal_direction", 0),
                    "interpretation": interp.get("interpretation", ""),
                    "created_at": "",
                    "source": tx.get("source", ""),
                })
        except Exception:
            pass

    return {
        "symbol": symbol,
        "signal": {
            "direction": signal.direction,
            "confidence": signal.confidence,
            "count": signal.activities_count,
            "total_usd": signal.total_usd,
            "summary": signal.summary,
        },
        "activities": activities,
    }


@router.post("/whale/fetch")
async def trigger_whale_fetch(db: Session = Depends(get_db)):
    """手动触发鲸鱼数据拉取"""
    from backend.services.whale_tracker_service import whale_tracker
    results = await whale_tracker.fetch_and_record(db)
    return {"fetched": len(results), "activities": results}


# ═══════════════════ 合约数据 ═══════════════════

@router.get("/derivatives/{symbol}")
def get_derivatives(symbol: str = "BTC"):
    """获取合约数据快照"""
    from backend.services.derivatives_analytics_service import derivatives_analytics
    snap = derivatives_analytics.get_snapshot(symbol)
    return {
        "symbol": symbol,
        "funding_rate": snap.funding_rate,
        "funding_rate_8h_avg": snap.funding_rate_8h_avg,
        "oi_total": snap.oi_total,
        "oi_change_1h": snap.oi_change_1h,
        "oi_change_24h": snap.oi_change_24h,
        "liquidation_1h_long": snap.liquidation_1h_long,
        "liquidation_1h_short": snap.liquidation_1h_short,
        "liquidation_ratio": snap.liquidation_ratio,
        "long_short_ratio": snap.long_short_ratio,
        "top_trader_ls_ratio": snap.top_trader_ls_ratio,
        "signal": snap.signal,
        "signal_strength": snap.signal_strength,
        "interpretation": snap.interpretation,
    }


# ═══════════════════ 多周期编排器 ═══════════════════

@router.get("/orchestrator/{symbol}")
def get_orchestrator_decision(symbol: str = "BTC"):
    """获取多周期编排器决策（含智能槽位推荐）"""
    from backend.services.multi_timeframe_orchestrator import mt_orchestrator
    decision = mt_orchestrator.evaluate(symbol)
    return {
        "symbol": symbol,
        "long_term": {
            "bias": decision.long_view.bias,
            "confidence": decision.long_view.confidence,
            "action": decision.long_view.suggested_action,
            "details": decision.long_view.details,
        },
        "mid_term": {
            "bias": decision.mid_view.bias,
            "confidence": decision.mid_view.confidence,
            "action": decision.mid_view.suggested_action,
            "details": decision.mid_view.details,
        },
        "short_term": {
            "bias": decision.short_view.bias,
            "confidence": decision.short_view.confidence,
            "action": decision.short_view.suggested_action,
            "details": decision.short_view.details,
        },
        "coordination": {
            "allowed_direction": decision.allowed_direction,
            "position_multiplier": decision.position_multiplier,
            "note": decision.coordination_note,
        },
        "final": {
            "action": decision.final_action,
            "side": decision.final_side,
            "position_pct": decision.final_position_pct,
            "leverage": decision.final_leverage,
            "sl_pct": decision.final_sl_pct,
            "tp_pct": decision.final_tp_pct,
            "reasoning": decision.reasoning,
        },
        "sentiment": {
            "index": decision.sentiment_index,
            "zone": decision.sentiment_zone,
        },
        "event_override": decision.event_note or None,
        "smart_slots": {
            "recommended": decision.recommended_slots,
            "actions": decision.slot_actions,
            "reasoning": decision.slot_reasoning,
        },
    }


# ═══════════════════ AI复盘 ═══════════════════

@router.get("/journal")
def get_journals(
    period_type: str = Query(default="daily"),
    limit: int = Query(default=20, le=100),
):
    """获取复盘记录"""
    from backend.services.ai_trade_journal_service import trade_journal
    return trade_journal.get_journals(period_type, limit)


@router.post("/journal/daily")
async def trigger_daily_review(date: Optional[str] = None):
    """手动触发日复盘"""
    from backend.services.ai_trade_journal_service import trade_journal
    result = await trade_journal.daily_review(date)
    return result


@router.post("/journal/weekly")
async def trigger_weekly_summary():
    """手动触发周总结"""
    from backend.services.ai_trade_journal_service import trade_journal
    result = await trade_journal.weekly_summary()
    return result


@router.post("/journal/monthly")
async def trigger_monthly_report():
    """手动触发月报告"""
    from backend.services.ai_trade_journal_service import trade_journal
    result = await trade_journal.monthly_report()
    return result


# ═══════════════════ 情报融合信号（核心） ═══════════════════

@router.get("/trading-signal/{symbol}")
def get_trading_signal(symbol: str = "BTC"):
    """获取融合后的交易方向信号 — 情报中心的核心输出"""
    import concurrent.futures
    from backend.services.intelligence_signal_engine import intelligence_signal_engine

    # 在独立线程中执行，带超时保护，避免外部API阻塞主线程
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(intelligence_signal_engine.compute_trading_signal, symbol)
            signal = future.result(timeout=15.0)
            return signal.to_dict()
    except concurrent.futures.TimeoutError:
        logger.warning(f"[Intel] compute_trading_signal({symbol}) 超时(15s)，返回缓存或空结果")
        # 尝试返回缓存
        cached = intelligence_signal_engine._cache.get(symbol.upper())
        if cached:
            return cached.to_dict()
        return {
            "symbol": symbol, "direction": "neutral", "confidence": 0,
            "risk_level": "normal", "ai_reasoning": "信号计算超时，请稍后重试",
            "data_sources": {"timeout": True},
        }
    except Exception as e:
        logger.error(f"[Intel] compute_trading_signal error: {e}")
        return {
            "symbol": symbol, "direction": "neutral", "confidence": 0,
            "risk_level": "normal", "ai_reasoning": f"信号计算失败: {e}",
            "data_sources": {"error": True},
        }


@router.get("/oi-regime/{symbol}")
def get_oi_regime(symbol: str = "BTC"):
    """获取OI四象限分析"""
    from backend.services.derivatives_analytics_service import derivatives_analytics
    return derivatives_analytics.get_oi_regime(symbol)


@router.get("/liquidation-clusters/{symbol}")
def get_liquidation_clusters(symbol: str = "BTC"):
    """获取清算聚集区分析"""
    from backend.services.derivatives_analytics_service import derivatives_analytics
    return derivatives_analytics.get_liquidation_clusters(symbol)


# ═══════════════════ 综合情报摘要 ═══════════════════

@router.get("/summary/{symbol}")
def get_intelligence_summary(symbol: str = "BTC"):
    """获取完整情报摘要"""
    from backend.services.unified_data_pool import unified_data_pool
    return unified_data_pool.get_intelligence_summary(symbol)
