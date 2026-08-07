"""
Parity Score API Routes — 回测/实盘一致性验证结果查询（规划文档§4.5 P3）。

供前端做"历史趋势可视化"（规划文档验收标准表要求项），以及供人工/运维核查该模块
确实在生产环境按周产出真实 Score，而不是写完代码没人跑。
"""
import logging
from typing import Optional

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/parity-score", tags=["parity-score"])


@router.get("/history")
def get_parity_history(
    nature: Optional[str] = Query(None, description="按nature过滤，如 scalp/swing/trend_follow"),
    limit: int = Query(200, ge=1, le=1000),
):
    """历史Parity Score记录（每次run_parity_score_pipeline调用都会追加一条/nature）。"""
    from backend.services.backtest_engine.parity_score import load_parity_history

    records = load_parity_history(nature=nature, limit=limit)
    return {"count": len(records), "records": records}


@router.post("/run")
def trigger_parity_score_run(
    nature: Optional[str] = Query(None, description="仅计算指定nature，留空则计算全部"),
    lookback_days: int = Query(7, ge=1, le=30),
):
    """手动触发一轮Parity Score计算（正常由每周cron任务`parity_score_weekly`自动触发，
    此接口用于人工核查/调试，同步执行，可能耗时数十秒到数分钟（含K线加载+回测回放）。"""
    from backend.services.backtest_engine.parity_score import run_parity_score_pipeline

    natures = [nature.lower()] if nature else None
    results = run_parity_score_pipeline(natures=natures, lookback_days=lookback_days)
    return {"results": results}
