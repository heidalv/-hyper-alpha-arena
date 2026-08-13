"""Rebate QAA / coordinator / macro filter 单元测试。"""

from __future__ import annotations

import pytest


def test_strategy_to_action_mapping():
    from backend.services.rebate_arb.qaa_strategy_constants import (
        strategy_to_executor_action,
    )

    assert strategy_to_executor_action("S8") == "execute_asterdex_rh"
    assert strategy_to_executor_action("S7") is None


def test_coordinator_mutex_hedge_vs_s8():
    from backend.services.rebate_arb.strategy_coordinator import rank_and_filter

    opps = [
        {"strategy_type": "S1", "is_viable": True, "expected_monthly_value": 100, "risk_score": 0.2, "confidence": 0.8},
        {"strategy_type": "S8", "is_viable": True, "expected_monthly_value": 150, "risk_score": 0.3, "confidence": 0.7},
    ]
    r = rank_and_filter(opps, active_strategy_ids=["S8"], account_equity=300)
    assert r["strategy_id"] != "S1"


def test_s3_hold_phase():
    from backend.services.rebate_arb.strategies.s3_points_mining import S3PointsMiningStrategy

    plan = S3PointsMiningStrategy().build_execution_plan(30.0)
    assert plan.get("hold_phase") is not None
    assert plan.get("close_plan") is not None


def test_macro_filter_neutral_allows():
    from backend.services.rebate_arb.macro_direction_filter import evaluate_macro_filter

    r = evaluate_macro_filter("ETH", "neutral")
    assert r.get("passed") is True


def test_volume_program_normalize():
    from backend.services.rebate_arb.volume_program_executor import normalize_volume_plan

    plan = normalize_volume_plan({"strategy": "S2", "exchange": "okx"}, 100.0)
    assert plan.get("side_a") is not None
    assert plan.get("hold_phase") is not None


def test_registry_s7_not_paper_auto():
    from backend.services.rebate_arb.strategy_runtime_registry import (
        is_paper_auto_executable,
        runtime_spec_to_dict,
    )

    assert is_paper_auto_executable("S7") is False
    s8 = runtime_spec_to_dict("S8")
    assert s8.get("macro_filter_required") is True
    assert "rebate_strategy_analyst" in (s8.get("qaa_agent_chain") or [])


def test_strategy_sub_pool_status():
    from backend.services.rebate_arb.capital_coordinator import capital_coordinator

    capital_coordinator.initialize(300.0)
    status = capital_coordinator.get_strategy_sub_pool_status()
    assert "S8" in status
    assert status["S8"]["cap_usd"] >= 0
