"""Rebate tick 统一参数与 prompt 上下文测试。"""

from backend.services.rebate_arb.tick_context import build_rebate_arb_context, get_last_rebate_arb_context
from backend.services.rebate_arb.trader_llm_resolver import resolve_rebate_tick_params


def test_resolve_rebate_tick_params_from_snapshot():
    snap = {
        "profile_id": 7,
        "enabled_strategies": ["S3", "S8"],
        "mode": "paper",
        "paper_account_mode": "dedicated_arbitrage_paper",
        "arbitrage_paper_account_id": 2,
    }
    params = resolve_rebate_tick_params(profile_snapshot=snap)
    assert params["trader_profile_id"] == 7
    assert params["enabled_strategies"] == ["S3", "S8"]
    assert params["arbitrage_paper_account_id"] == 2


def test_build_rebate_arb_context_summary():
    ctx_data = {"account_equity": 300.0, "incentive_data": {}, "funding_rates": {}}
    profile = {
        "trader_profile_id": 1,
        "enabled_strategies": ["S3", "S8"],
        "account_name": "测试交易员",
    }
    opportunities = [
        {
            "strategy_type": "S8",
            "is_viable": True,
            "expected_monthly_value": 45.0,
            "risk_score": 0.3,
            "confidence": 0.7,
        }
    ]
    out = build_rebate_arb_context(ctx_data, profile, opportunities)
    assert "S3" in out["summary_text"]
    assert "S8" in out["summary_text"]
    assert out["top_opportunities"][0]["strategy_type"] == "S8"
    cached = get_last_rebate_arb_context()
    assert cached["summary_text"] == out["summary_text"]
