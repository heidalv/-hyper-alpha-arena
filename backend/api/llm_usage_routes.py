"""
LLM Usage Statistics API

Provides endpoints for viewing model usage, costs, and pricing information.
"""
import logging
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.database.connection import get_analytics_db
from backend.services.llm_usage_service import (
    get_usage_summary,
    get_pricing_table,
    get_billing_dashboard,
    get_deepseek_official_pricing,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/llm-usage", tags=["LLM Usage"])


@router.get("/summary")
async def usage_summary(
    days: int = Query(30, ge=1, le=365, description="Number of days to aggregate"),
    db: Session = Depends(get_analytics_db),
):
    """Return aggregated usage statistics grouped by model."""
    try:
        return get_usage_summary(db, days=days)
    except Exception as e:
        logger.error("Failed to get usage summary: %s", e)
        return {
            "period_days": days,
            "grand_total_cost_usd": 0,
            "grand_total_calls": 0,
            "grand_total_tokens": 0,
            "models": [],
            "daily": [],
        }


@router.get("/billing")
async def billing_dashboard(
    days: int = Query(30, ge=1, le=365, description="统计天数"),
    db: Session = Depends(get_analytics_db),
):
    """详细计费仪表盘：总览、DeepSeek 官方价、按交易员/模型/场景统计。"""
    try:
        return get_billing_dashboard(db, days=days)
    except Exception as e:
        logger.error("Failed to get billing dashboard: %s", e)
        return {
            "period_days": days,
            "cny_usd_rate": 7.25,
            "deepseek_official": get_deepseek_official_pricing(),
            "summary": {
                "total_calls": 0,
                "failed_calls": 0,
                "success_calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0,
                "cost_cny": 0,
                "avg_cost_cny_per_call": 0,
            },
            "deepseek_summary": {"total_calls": 0, "cost_usd": 0, "cost_cny": 0, "models": []},
            "traders": [],
            "modules": [],
            "cache_summary": {
                "cache_hit_tokens": 0,
                "cache_miss_tokens": 0,
                "cache_hit_rate": 0,
                "cost_cny_actual": 0,
                "cost_cny_if_all_miss": 0,
                "cache_savings_cny": 0,
                "has_cache_breakdown": False,
            },
            "models": [],
            "call_types": [],
            "daily": [],
            "recent_calls": [],
            "error": str(e),
        }


@router.get("/pricing")
async def pricing_info():
    """Return the pricing table for all known models."""
    return {
        "pricing": get_pricing_table(),
        "deepseek_official": get_deepseek_official_pricing(),
    }
