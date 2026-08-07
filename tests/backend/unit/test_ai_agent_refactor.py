"""AI 策略 Agent 系统重构 — 单元测试。"""
import os
import sys

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


class TestAgentEntrypointMap:
    def test_canonical_pipeline_has_five_stages(self):
        from backend.services.ai_agent_entrypoint_map import (
            PipelineStage,
            get_canonical_pipeline,
        )

        stages = get_canonical_pipeline()
        assert len(stages) == 5
        assert PipelineStage.SIZING in stages

    def test_active_entrypoints_includes_primary_loop(self):
        from backend.services.ai_agent_entrypoint_map import get_active_entry_points

        ids = [ep.id for ep in get_active_entry_points()]
        primary = {
            "unified_loop_ai_first",
            "unified_loop_qaa_v3",
            "unified_loop_qaa_legacy",
        }
        assert primary & set(ids), f"期望至少一个主循环入口，实际: {ids}"
        # 2026-06-19: DUAL_AGENT_MODE 默认改 off（DirectionAgent 已废弃），
        # 不再断言 dual_agent_primary，改为确认主循环入口存在即可。

    def test_audit_missing_sizing_fields(self):
        from backend.services.ai_agent_entrypoint_map import audit_decision_sizing_fields

        warn = audit_decision_sizing_fields({"symbol": "BTC", "action": "buy"})
        assert warn is not None
        assert "PositionSizingAgent" in warn

    def test_audit_hold_skipped(self):
        from backend.services.ai_agent_entrypoint_map import audit_decision_sizing_fields

        assert audit_decision_sizing_fields({"symbol": "BTC", "action": "hold"}) is None


# TestMasterPromptAudit 已删除：ai_master_prompt_audit 模块为零引用死代码，
# 于 2026-06-11 全面审查中随模块一并移除。


class TestTradePerformanceAnalyzer:
    def test_analyze_from_db_path(self):
        from backend.services.trade_performance_analyzer import analyze_closed_trades

        db_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "data", "alpha_arena.db"
        )
        if not os.path.isfile(db_path):
            pytest.skip("alpha_arena.db 不存在")
        report = analyze_closed_trades(db_path=db_path)
        assert report.total_closed >= 0
        if report.total_closed > 0:
            assert report.by_close_reason

    def test_render_markdown(self):
        from backend.services.trade_performance_analyzer import (
            TradePerformanceReport,
            render_report_markdown,
        )

        md = render_report_markdown(TradePerformanceReport(total_closed=0))
        assert "交易盈亏归因报告" in md


class TestAgentPipelineContracts:
    def test_risk_cannot_amplify(self):
        from backend.services.agent_pipeline_contracts import validate_risk_cannot_amplify

        err = validate_risk_cannot_amplify(
            {"position_pct": 0.15, "leverage": 12},
            {"position_pct": 0.10, "leverage": 10},
        )
        assert err is not None

    def test_build_audit_trail(self):
        from backend.services.agent_pipeline_contracts import build_audit_trail

        trail = build_audit_trail(
            {
                "symbol": "ETH",
                "action": "buy",
                "leverage": 10,
                "position_pct": 0.08,
                "_sizing_source": "risk_budget",
            }
        )
        assert trail.symbol == "ETH"
        assert trail.sizing_source == "risk_budget"


class TestAiPromptLayers:
    def test_evidence_score_can_open(self):
        from backend.services.ai_prompt_layers import compute_evidence_score

        result = compute_evidence_score(
            tier_confidence=60,
            debate_delta=2,
            orchestrator_aligned=True,
            prefilter_passed=True,
            template_confidence=55,
            is_short_tier_scalp=False,
        )
        assert result["can_open"] is True
        assert result["score"] >= 50

    def test_evidence_score_template_veto(self):
        from backend.services.ai_prompt_layers import compute_evidence_score

        result = compute_evidence_score(
            tier_confidence=80,
            debate_delta=3,
            orchestrator_aligned=True,
            prefilter_passed=True,
            template_confidence=20,
            is_short_tier_scalp=False,
        )
        assert result["vetoed"] is True
        assert result["can_open"] is False

    def test_build_layered_prompt_contains_layers(self):
        from backend.services.ai_prompt_layers import (
            LayeredPromptContext,
            build_layered_master_prompt,
        )

        prompt = build_layered_master_prompt(
            LayeredPromptContext(
                report_text="test report",
                debate_text="debate",
                symbols_text="BTC",
            )
        )
        assert "方向判断层" in prompt
        assert "持仓管理层" in prompt
        assert "风控审核层" in prompt


class TestShortTierEntryGate:
    def test_blocks_low_confidence_short(self):
        from backend.services.short_tier_entry_gate import check_short_tier_entry

        r = check_short_tier_entry(
            account_id=1, symbol="BTC", side="buy", action="buy",
            confidence=20, tier="short", trade_nature="intraday",
            base_entry_threshold=50,
        )
        assert not r.allowed
        assert r.adjusted_threshold == 58

    def test_allows_high_confidence_short(self):
        from backend.services.short_tier_entry_gate import check_short_tier_entry

        r = check_short_tier_entry(
            account_id=1, symbol="ETH", side="buy", action="buy",
            confidence=65, tier="short", trade_nature="intraday",
            base_entry_threshold=50,
        )
        assert r.allowed

    def test_mid_tier_passes_through(self):
        from backend.services.short_tier_entry_gate import check_short_tier_entry

        r = check_short_tier_entry(
            account_id=1, symbol="BTC", side="buy", action="buy",
            confidence=40, tier="mid", trade_nature="swing",
        )
        assert r.allowed


class TestStrategyOfflineReplay:
    def test_replay_metrics(self):
        import os
        from backend.services.strategy_offline_replay import replay_closed_positions

        db_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "data", "alpha_arena.db"
        )
        if not os.path.isfile(db_path):
            pytest.skip("alpha_arena.db 不存在")
        m = replay_closed_positions(db_path)
        assert m.total_trades >= 0


class TestPositionSizingPlannerMerge:
    def test_risk_size_multiplier_reduces_position(self):
        from backend.services.position_sizing_agent import (
            PositionSizingInput,
            position_sizing_agent,
        )

        base = position_sizing_agent.build_plan(
            PositionSizingInput(
                symbol="BTC", side="buy", price=50000,
                confidence=0.7, total_equity=10000, available_balance=8000,
                tier="mid", trade_nature="swing",
            )
        )
        reduced = position_sizing_agent.build_plan(
            PositionSizingInput(
                symbol="BTC", side="buy", price=50000,
                confidence=0.7, total_equity=10000, available_balance=8000,
                tier="mid", trade_nature="swing", size_multiplier=0.5,
            )
        )
        assert reduced.position_pct <= base.position_pct

    def test_leverage_cap_applied(self):
        from backend.services.position_sizing_agent import (
            PositionSizingInput,
            position_sizing_agent,
        )

        plan = position_sizing_agent.build_plan(
            PositionSizingInput(
                symbol="ETH", side="buy", price=3000,
                confidence=0.9, total_equity=10000, available_balance=8000,
                requested_leverage=18, leverage_cap=10,
                tier="mid", trade_nature="swing",
            )
        )
        assert plan.leverage <= 10

    def test_trailing_config_on_plan(self):
        from backend.services.position_sizing_agent import (
            PositionSizingInput,
            position_sizing_agent,
        )

        plan = position_sizing_agent.build_plan(
            PositionSizingInput(
                symbol="ETH", side="buy", price=3000,
                confidence=0.7, total_equity=10000, available_balance=8000,
                tier="short", trade_nature="scalp", volatility_pct=0.02,
            )
        )
        assert plan.trailing_activation_pct > 0
        assert plan.position_pct <= 0.08


class TestDecisionFeedbackService:
    def test_build_feedback_empty_db(self):
        from backend.services.decision_feedback_service import decision_feedback_service

        bundle = decision_feedback_service.build_feedback(db=None)
        assert bundle is not None
        assert isinstance(bundle.policy_adjustments, list)

    def test_prompt_injection_cached(self):
        from backend.services.decision_feedback_service import decision_feedback_service

        t1 = decision_feedback_service.get_prompt_injection(db=None)
        t2 = decision_feedback_service.get_prompt_injection(db=None)
        assert t1 == t2
