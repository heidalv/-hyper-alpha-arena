"""
Evolution System API Routes

进化系统REST API：
  GET  /api/evolution/status              — 进化系统完整状态
  POST /api/evolution/trigger/{type}      — 触发进化
  GET  /api/evolution/correlation-matrix  — 币种相关性矩阵
  GET  /api/evolution/regime-analysis     — 当前市场状态分析
  GET  /api/evolution/history             — 进化历史（分页）
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/evolution", tags=["Evolution"])


@router.get("/status")
async def get_evolution_status():
    """进化系统完整状态"""
    try:
        from backend.services.strategy_evolver import StrategyEvolver
        from backend.services.evolution_scheduler import evolution_scheduler

        evolver = StrategyEvolver()
        return {
            "evolver_running": evolver.is_running or evolution_scheduler._running_evolution,
            "evolver_progress": evolver.progress,
            "scheduler_status": "active" if not evolution_scheduler._running_evolution else "evolving",
        }
    except Exception as e:
        logger.error(f"[Evolution] status error: {e}")
        return {"evolver_running": False, "evolver_progress": {}, "error": str(e)}


@router.post("/trigger/{trigger_type}")
async def trigger_evolution(
    trigger_type: str,  # "manual" | "auto" | "emergency"
    template_id: Optional[str] = None,
):
    """触发进化（支持指定模板）"""
    try:
        if trigger_type == "emergency":
            from backend.services.evolution_scheduler import evolution_scheduler
            evolution_scheduler.trigger_emergency_evolution(
                template_id=template_id,
                reason=f"手动紧急进化: template={template_id}",
            )
            return {"success": True, "message": f"紧急进化已触发: {template_id}"}

        elif trigger_type in ("manual", "auto"):
            from backend.services.strategy_evolver import StrategyEvolver
            evolver = StrategyEvolver()
            if evolver.is_running:
                return {"success": False, "message": "进化已在运行中"}
            result = evolver.start_evolution()
            return {"success": result.get("success", False), "message": result.get("message", "")}

        else:
            raise HTTPException(status_code=400, detail=f"Unknown trigger type: {trigger_type}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Evolution] trigger error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/correlation-matrix")
async def get_correlation_matrix():
    """获取币种相关性矩阵"""
    try:
        from backend.services.rl.portfolio_risk_aggregator import portfolio_risk_aggregator

        cache = portfolio_risk_aggregator._correlation_cache
        if not cache or 'matrix' not in cache:
            return {"symbols": [], "matrix": [], "computed_at": None}

        matrix = cache['matrix']
        symbols = cache.get('symbols', [])
        computed_at = cache.get('computed_at', 0)

        # 转换为JSON可序列化格式
        matrix_data = []
        if hasattr(matrix, 'values'):
            for i, row in enumerate(matrix.values):
                matrix_data.append([float(v) for v in row])
        else:
            for row in matrix:
                matrix_data.append([float(v) for v in row])

        return {
            "symbols": symbols,
            "matrix": matrix_data,
            "computed_at": computed_at,
        }
    except Exception as e:
        logger.error(f"[Evolution] correlation matrix error: {e}")
        return {"symbols": [], "matrix": [], "error": str(e)}


def _is_missing_table_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return (
        "undefinedtable" in type(exc).__name__.lower()
        or "does not exist" in msg
        or "不存在" in msg
        or "no such table" in msg
    )


@router.get("/regime-analysis")
async def get_regime_analysis(
    window_minutes: int = Query(30, ge=5, le=720, description="聚合窗口（分钟）"),
    anchor_symbol: str = Query("BTC", description="主市场锚定 symbol，用于给 current_regime 结果做决胜"),
):
    """获取当前市场状态分析

    v3 整改：协调器状态表（`system_coordinator_states.current_regime`）与
    `market_regime_history` 表尚未接入写入链路，为避免前端长期显示"暂无"，
    此处降级读取 `market_analysis_snapshots`（FullAuto 主循环 90s 写一次）并做多数投票。

    聚合规则：
      - 取 window_minutes 内每个 symbol 的最新一条 snapshot
      - 对 regime_type 做多数投票得到 current_regime
      - 平票时以 anchor_symbol（默认 BTC）的 regime 为准
      - regime_confidence = 投票 symbol 的 regime_confidence 平均
      - regime_distribution = 每个 regime_type 出现次数
    """
    try:
        from backend.database.connection import SessionLocal, AnalyticsSessionLocal
        from backend.database.models import (
            SystemCoordinatorState,
            MarketRegimeHistory,
            MarketAnalysisSnapshot,
        )
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import func as _sa_func

        coordinator_regime = None
        coordinator_confidence = 0.0
        regime_distribution: dict[str, int] = {}
        source = "market_analysis_snapshots"
        sample_count = 0

        core_db = SessionLocal()
        analytics_db = AnalyticsSessionLocal()
        try:
            # 1) 协调器状态在 Core DB（非 Analytics）
            try:
                state = core_db.query(SystemCoordinatorState).first()
                if state:
                    coordinator_regime = state.current_regime
                    coordinator_confidence = float(state.regime_confidence or 0.0)
                    source = "coordinator_state"
            except Exception as coord_err:
                if _is_missing_table_error(coord_err):
                    logger.debug(
                        "[Evolution] system_coordinator_state missing, use snapshot fallback"
                    )
                else:
                    raise

            # 2) MarketRegimeHistory 同样在 Core DB
            try:
                recent_regimes = core_db.query(MarketRegimeHistory).order_by(
                    MarketRegimeHistory.created_at.desc()
                ).limit(20).all()
                for r in recent_regimes:
                    regime_distribution[r.regime] = regime_distribution.get(r.regime, 0) + 1
                sample_count = len(recent_regimes)
            except Exception as hist_err:
                if _is_missing_table_error(hist_err):
                    logger.debug(
                        "[Evolution] market_regime_history missing, use snapshot fallback"
                    )
                else:
                    raise

            # 3) 降级：从 market_analysis_snapshots（Analytics DB）聚合
            if not coordinator_regime or not regime_distribution:
                # 修时区 bug：用 UTC-aware datetime 算 Unix 毫秒（与写入端保持一致）
                cutoff_ms = int(
                    (datetime.now(timezone.utc) - timedelta(minutes=window_minutes)).timestamp() * 1000
                )
                # 每 symbol 最近 1 条
                subq = (
                    analytics_db.query(
                        MarketAnalysisSnapshot.symbol.label("sym"),
                        _sa_func.max(MarketAnalysisSnapshot.timestamp).label("ts_max"),
                    )
                    .filter(MarketAnalysisSnapshot.timestamp >= cutoff_ms)
                    .group_by(MarketAnalysisSnapshot.symbol)
                    .subquery()
                )
                latest_rows = (
                    analytics_db.query(MarketAnalysisSnapshot)
                    .join(
                        subq,
                        (MarketAnalysisSnapshot.symbol == subq.c.sym)
                        & (MarketAnalysisSnapshot.timestamp == subq.c.ts_max),
                    )
                    .all()
                )

                # 兜底：若时区修复瞬间窗口内没数据（存量旧数据时间戳可能偏 ±TZ offset）
                # → 直接取"最近 N 条 per-symbol"，不受时间窗口约束，保证 UI 永不空白
                if not latest_rows:
                    fallback_sub = (
                        analytics_db.query(
                            MarketAnalysisSnapshot.symbol.label("sym"),
                            _sa_func.max(MarketAnalysisSnapshot.timestamp).label("ts_max"),
                        )
                        .group_by(MarketAnalysisSnapshot.symbol)
                        .subquery()
                    )
                    latest_rows = (
                        analytics_db.query(MarketAnalysisSnapshot)
                        .join(
                            fallback_sub,
                            (MarketAnalysisSnapshot.symbol == fallback_sub.c.sym)
                            & (MarketAnalysisSnapshot.timestamp == fallback_sub.c.ts_max),
                        )
                        .limit(50)
                        .all()
                    )
                    if latest_rows:
                        source_suffix = "(latest-per-symbol-fallback)"
                    else:
                        source_suffix = ""
                else:
                    source_suffix = ""

                if latest_rows:
                    vote: dict[str, int] = {}
                    conf_by_regime: dict[str, list[float]] = {}
                    anchor_regime: Optional[str] = None
                    for row in latest_rows:
                        rt = (row.regime_type or "unknown").strip() or "unknown"
                        vote[rt] = vote.get(rt, 0) + 1
                        conf_by_regime.setdefault(rt, []).append(
                            float(row.regime_confidence or 0.0)
                        )
                        if row.symbol == anchor_symbol:
                            anchor_regime = rt

                    # 决胜：票数最高；平票用 anchor；再平票用字典序
                    top_count = max(vote.values())
                    top_regimes = [k for k, v in vote.items() if v == top_count]
                    if len(top_regimes) == 1:
                        winner = top_regimes[0]
                    elif anchor_regime and anchor_regime in top_regimes:
                        winner = anchor_regime
                    else:
                        winner = sorted(top_regimes)[0]

                    avg_conf = (
                        sum(conf_by_regime.get(winner, []))
                        / max(1, len(conf_by_regime.get(winner, [])))
                    )

                    coordinator_regime = winner
                    coordinator_confidence = round(float(avg_conf), 4)
                    if source_suffix:
                        # 兜底模式：分布直接用 latest_rows 的 vote，无法走窗口 count
                        regime_distribution = {
                            (rt or "unknown"): int(cnt) for rt, cnt in vote.items()
                        }
                        sample_count = sum(regime_distribution.values())
                        source = f"market_analysis_snapshots{source_suffix}"
                    else:
                        # 分布用全部窗口里所有 snapshot 计数更直观（不仅 latest-per-symbol）
                        window_rows = (
                            analytics_db.query(
                                MarketAnalysisSnapshot.regime_type,
                                _sa_func.count(MarketAnalysisSnapshot.id),
                            )
                            .filter(MarketAnalysisSnapshot.timestamp >= cutoff_ms)
                            .group_by(MarketAnalysisSnapshot.regime_type)
                            .all()
                        )
                        regime_distribution = {
                            (rt or "unknown"): int(cnt) for rt, cnt in window_rows
                        }
                        sample_count = sum(regime_distribution.values())
                        source = f"market_analysis_snapshots(window={window_minutes}m)"

            return {
                "current_regime": coordinator_regime,
                "regime_confidence": coordinator_confidence,
                "regime_distribution": regime_distribution,
                "recent_count": sample_count,
                "source": source,
                "anchor_symbol": anchor_symbol,
            }
        finally:
            core_db.close()
            analytics_db.close()
    except Exception as e:
        if _is_missing_table_error(e):
            logger.warning("[Evolution] regime analysis degraded (missing table): %s", e)
        else:
            logger.error(f"[Evolution] regime analysis error: {e}")
        return {"current_regime": None, "regime_distribution": {}, "error": str(e)}


@router.get("/history")
async def get_evolution_history(
    template_id: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """进化历史（分页）"""
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import BacktestRun

        db = SessionLocal()
        try:
            query = db.query(BacktestRun).filter(
                BacktestRun.generation.isnot(None)
            )
            if template_id:
                query = query.filter(BacktestRun.template_id == template_id)

            total = query.count()
            records = query.order_by(
                BacktestRun.created_at.desc()
            ).offset((page - 1) * page_size).limit(page_size).all()

            return {
                "total": total,
                "page": page,
                "page_size": page_size,
                "records": [
                    {
                        "run_id": r.run_id,
                        "template_id": r.template_id,
                        "symbol": r.symbol,
                        "generation": r.generation,
                        "sharpe_ratio": r.sharpe_ratio,
                        "win_rate": r.win_rate,
                        "max_drawdown": r.max_drawdown,
                        "total_return": r.total_return,
                        "is_champion": r.is_champion,
                        "status": r.status,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    }
                    for r in records
                ],
            }
        finally:
            db.close()
    except Exception as e:
        logger.error(f"[Evolution] history error: {e}")
        return {"total": 0, "page": page, "page_size": page_size, "records": [], "error": str(e)}
