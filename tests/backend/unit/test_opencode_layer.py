"""OpenCode 智能分析层 — 单元与集成测试。"""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest


class TestStrategyRuntimeReport:
    def test_generate_ai_report_empty_db(self):
        from backend.database.connection import SessionLocal
        from backend.services.strategy_runtime_report import build_ai_report, save_report

        db = SessionLocal()
        try:
            report = build_ai_report(db, window="24h")
            assert report.domain == "ai"
            assert report.window == "24h"
            assert report.total_closed >= 0
            path = save_report(report)
            assert os.path.isfile(path)
            latest = os.path.join("data", "strategy_runtime_reports", "latest_24h_ai.json")
            assert os.path.isfile(latest)
        finally:
            db.close()

    def test_rule_insight_major_master_close(self):
        from backend.services.strategy_runtime_report import StrategyRuntimeReport, _derive_rule_insights

        report = StrategyRuntimeReport(
            window="24h", domain="ai", generated_at="",
            total_closed=30, win_rate=0.35,
            master_close_loss_ratio=0.65, master_close_count=10,
        )
        insights = _derive_rule_insights(report)
        severities = {i.severity for i in insights}
        assert "major" in severities


class TestPaperPaceController:
    def test_gear_knobs(self):
        from backend.services.paper_pace_controller import paper_pace_controller, GEARS

        paper_pace_controller.set_gear("turbo", manual=False)
        assert paper_pace_controller.gear == "turbo"
        assert paper_pace_controller.get_tick_seconds() == 45

        paper_pace_controller.force_downshift(1, reason="test")
        assert paper_pace_controller.gear in GEARS

    def test_manual_lock_blocks_eval(self):
        from backend.services.paper_pace_controller import paper_pace_controller

        paper_pace_controller.set_gear("conservative", manual=True)
        assert paper_pace_controller.evaluate_from_reports() is None
        paper_pace_controller.unlock_manual()


class TestRuntimeTuningStore:
    def test_apply_and_rollback(self):
        from backend.services import runtime_tuning_store as rts

        with tempfile.TemporaryDirectory() as tmp:
            tuning_file = os.path.join(tmp, "runtime_tuning.json")
            snap_dir = os.path.join(tmp, "snaps")
            old_tuning = rts.TUNING_FILE
            old_snap = rts.SNAPSHOT_DIR
            rts.TUNING_FILE = tuning_file
            rts.SNAPSHOT_DIR = snap_dir
            rts.invalidate_cache()
            try:
                applied = rts.apply_patches({"master_reduce_min_loss_pct": 0.08}, proposal_id=99)
                assert applied["master_reduce_min_loss_pct"] == 0.08
                assert rts.get_tuning_float("master_reduce_min_loss_pct", 0.05) == 0.08
                assert rts.rollback_snapshot(99) is True
                rts.invalidate_cache()
                assert rts.get_tuning_float("master_reduce_min_loss_pct", 0.05) == 0.10
            finally:
                rts.TUNING_FILE = old_tuning
                rts.SNAPSHOT_DIR = old_snap
                rts.invalidate_cache()

    def test_clamp_rejects_out_of_bounds(self):
        from backend.services import runtime_tuning_store as rts

        with tempfile.TemporaryDirectory() as tmp:
            old = rts.TUNING_FILE
            rts.TUNING_FILE = os.path.join(tmp, "t.json")
            rts.invalidate_cache()
            try:
                applied = rts.apply_patches({"master_reduce_min_loss_pct": 0.99}, proposal_id=1)
                assert applied["master_reduce_min_loss_pct"] <= 0.20
            finally:
                rts.TUNING_FILE = old
                rts.invalidate_cache()


class TestDecisionPolicyEngine:
    def test_block_reduce_small_drawdown(self):
        from backend.services.decision_policy_engine import evaluate

        r = evaluate("master_close", {
            "action": "reduce",
            "floating_loss_pct": 0.02,
            "risk_score": 50,
            "sl_breach_ratio": 0.5,
        })
        assert r.effect == "block"
        assert r.rule_id == "block_reduce_on_small_drawdown"

    def test_allow_sl_breach(self):
        from backend.services.decision_policy_engine import evaluate

        r = evaluate("master_close", {
            "action": "close",
            "floating_loss_pct": 0.01,
            "sl_breach_ratio": 1.6,
        })
        assert r.effect == "allow"


class TestOpenCodeActionRouter:
    def test_merge_opencode_lessons(self):
        from backend.services.opencode_action_router import merge_opencode_lessons

        mem = MagicMock()
        mem.key_lessons = []
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mem

        with patch("backend.database.connection.sqlite_write_commit"):
            n = merge_opencode_lessons(
                mock_db,
                [{"message": "test opencode lesson mock", "category": "master_close"}],
                severity="minor",
            )
        assert n >= 1
        sources = [l.get("source") for l in mem.key_lessons if isinstance(l, dict)]
        assert "opencode" in sources


class TestOpenCodeBridge:
    def test_extract_json_from_markdown(self):
        from backend.services.opencode_bridge import _extract_json

        raw = 'Here is result:\n{"severity": "minor", "findings": []}'
        out = _extract_json(raw)
        assert out["severity"] == "minor"

    def test_parse_model_slug(self):
        from backend.services.opencode_bridge import _parse_model_slug

        assert _parse_model_slug("deepseek/deepseek-v4-pro") == ("deepseek", "deepseek-v4-pro")
        assert _parse_model_slug("deepseek-v4-flash") == ("deepseek", "deepseek-v4-flash")

    def test_disabled_returns_skipped(self):
        from backend.services.opencode_bridge import run_plan_analysis

        with patch("backend.services.opencode_bridge._is_enabled", return_value=False):
            out = run_plan_analysis("data/opencode_reports/mock_context.json")
            assert out.get("skipped") == "OPENCODE_ENABLED=false"

    def test_scheduled_analysis_skips_when_insufficient_data(self):
        from backend.services.opencode_bridge import run_scheduled_analysis

        fake_pack = {
            "window": "24h",
            "domain": "ai",
            "data_quality": {
                "runtime_report_total_closed": 0,
                "sufficient_for_analysis": False,
            },
            "runtime_report": {"total_closed": 0},
        }
        with patch("backend.services.opencode_context_pack.build_context_pack", return_value=fake_pack), \
             patch("backend.services.opencode_context_pack.save_context_pack", return_value="/tmp/ctx.json"), \
             patch("backend.services.opencode_bridge.run_plan_analysis") as mock_plan:
            out = run_scheduled_analysis(MagicMock(), window="24h", domain="ai")
            assert out.get("skipped") == "insufficient_trade_data"
            mock_plan.assert_not_called()


class TestOpenCodeContextPack:
    def test_build_context_pack(self):
        from backend.database.connection import SessionLocal
        from backend.services.opencode_context_pack import build_context_pack, save_context_pack

        db = SessionLocal()
        try:
            pack = build_context_pack(db, window="24h", domain="ai")
            assert pack["window"] == "24h"
            assert "runtime_report" in pack
            assert "data_quality" in pack
            assert "pace_gear" in pack
            rr = pack["runtime_report"]
            assert int(rr.get("total_closed") or 0) >= 0
            dq = pack["data_quality"]
            assert dq["runtime_report_total_closed"] == int(rr.get("total_closed") or 0)
            path = save_context_pack(pack)
            assert os.path.isfile(path)
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            assert loaded["domain"] == "ai"
            assert int(loaded.get("runtime_report", {}).get("total_closed") or 0) == dq[
                "runtime_report_total_closed"
            ]
        finally:
            db.close()

    def test_empty_latest_cache_regenerates_when_db_has_trades(self):
        import json
        import os
        from backend.database.connection import SessionLocal
        from backend.services.strategy_runtime_report import (
            REPORT_DIR,
            get_or_build_runtime_report,
            load_latest_report,
        )

        db = SessionLocal()
        try:
            latest_path = os.path.join(REPORT_DIR, "latest_24h_ai.json")
            os.makedirs(REPORT_DIR, exist_ok=True)
            with open(latest_path, "w", encoding="utf-8") as f:
                json.dump({"window": "24h", "domain": "ai", "total_closed": 0}, f)
            assert int((load_latest_report("24h", "ai") or {}).get("total_closed") or 0) == 0
            rebuilt = get_or_build_runtime_report(db, "24h", "ai")
            if rebuilt.get("total_closed", 0) > 0:
                assert int(rebuilt["total_closed"]) > 0
                assert int((load_latest_report("24h", "ai") or {}).get("total_closed") or 0) > 0
        finally:
            db.close()


class TestOpenCodeProposalApplier:
    def test_create_tuning_proposal(self):
        from backend.database.connection import SessionLocal
        from backend.services.opencode_proposal_applier import create_proposal
        from backend.database.models import OpenCodeEvolutionProposalDB

        db = SessionLocal()
        try:
            pid = create_proposal(
                db,
                [{"key": "master_reduce_min_loss_pct", "value": 0.07, "type": "tuning"}],
                title="test proposal",
            )
            if pid:
                row = db.query(OpenCodeEvolutionProposalDB).filter(OpenCodeEvolutionProposalDB.id == pid).first()
                assert row is not None
                assert row.status == "pending"
        finally:
            db.close()

    def test_major_creates_pending_only(self):
        from backend.database.connection import SessionLocal
        from backend.services.opencode_proposal_applier import create_proposal
        from backend.database.models import OpenCodeEvolutionProposalDB

        db = SessionLocal()
        try:
            pid = create_proposal(
                db,
                [{"key": "max_daily_trades", "value": 10, "type": "tuning"}],
                severity="major",
                title="major test",
            )
            assert pid is not None
            row = db.query(OpenCodeEvolutionProposalDB).filter(OpenCodeEvolutionProposalDB.id == pid).first()
            assert row is not None
            assert row.status == "pending"
            assert row.severity == "major"
        finally:
            db.close()


class TestOpenCodeProposalReviewer:
    def test_hard_validation_rejects_bad_key(self):
        from backend.services.opencode_proposal_reviewer import validate_patches_hard

        ok, errors = validate_patches_hard([{"key": "forbidden_key", "value": 1, "type": "tuning"}])
        assert ok is False
        assert any("whitelist" in e for e in errors)

    def test_hard_validation_rejects_shadow_py(self):
        from backend.services.opencode_proposal_reviewer import validate_patches_hard

        ok, errors = validate_patches_hard([{"key": "x", "value": "y", "type": "shadow_py"}])
        assert ok is False
        assert any("shadow" in e for e in errors)

    def test_review_and_apply_hard_reject_no_llm(self):
        from backend.database.connection import SessionLocal
        from backend.services.opencode_proposal_applier import create_proposal
        from backend.services.opencode_proposal_reviewer import review_and_apply_proposal
        from backend.database.models import OpenCodeEvolutionProposalDB

        db = SessionLocal()
        try:
            pid = create_proposal(
                db,
                [{"key": "not_whitelisted", "value": 99, "type": "tuning"}],
                title="hard reject test",
            )
            assert pid is not None
            with patch(
                "backend.services.opencode_proposal_reviewer.run_review_agent",
            ) as mock_agent:
                out = review_and_apply_proposal(db, pid)
                mock_agent.assert_not_called()
            assert out["status"] == "rejected"
            row = db.query(OpenCodeEvolutionProposalDB).filter(OpenCodeEvolutionProposalDB.id == pid).first()
            assert row.status == "rejected"
        finally:
            db.close()

    def test_review_approve_applies(self):
        from backend.database.connection import SessionLocal
        from backend.services.opencode_proposal_applier import create_proposal
        from backend.services.opencode_proposal_reviewer import review_and_apply_proposal
        from backend.database.models import OpenCodeEvolutionProposalDB

        db = SessionLocal()
        try:
            patches = [{"key": "max_daily_trades", "value": 10, "type": "tuning"}]
            pid = create_proposal(db, patches, title="approve test", severity="minor")
            assert pid is not None
            mock_review = {
                "decision": "approve",
                "confidence": 0.9,
                "approved_patches": patches,
                "reasons": ["ok"],
                "risks": [],
            }
            with patch(
                "backend.services.opencode_proposal_reviewer.run_review_agent",
                return_value=mock_review,
            ):
                out = review_and_apply_proposal(db, pid)
            assert out["status"] == "paper_applying"
            row = db.query(OpenCodeEvolutionProposalDB).filter(OpenCodeEvolutionProposalDB.id == pid).first()
            assert row.status == "paper_applying"
        finally:
            db.close()

    def test_review_reject_downshifts(self):
        from backend.database.connection import SessionLocal
        from backend.services.opencode_proposal_applier import create_proposal
        from backend.services.opencode_proposal_reviewer import review_and_apply_proposal
        from backend.services.paper_pace_controller import paper_pace_controller
        from backend.database.models import OpenCodeEvolutionProposalDB

        db = SessionLocal()
        try:
            paper_pace_controller.set_gear("turbo", manual=False)
            pid = create_proposal(
                db,
                [{"key": "max_daily_trades", "value": 10, "type": "tuning"}],
                title="reject test",
                severity="minor",
            )
            mock_review = {
                "decision": "reject",
                "confidence": 0.95,
                "reasons": ["too risky"],
                "risks": ["drawdown"],
            }
            with patch(
                "backend.services.opencode_proposal_reviewer.run_review_agent",
                return_value=mock_review,
            ):
                out = review_and_apply_proposal(db, pid)
            assert out["status"] == "rejected"
            row = db.query(OpenCodeEvolutionProposalDB).filter(OpenCodeEvolutionProposalDB.id == pid).first()
            assert row.status == "rejected"
            assert paper_pace_controller.gear != "turbo"
        finally:
            paper_pace_controller.set_gear("turbo", manual=False)
            db.close()

    def test_minor_routes_through_review_not_direct_apply(self):
        from backend.database.connection import SessionLocal
        from backend.services.opencode_action_router import route_analysis_result
        from backend.database.models import OpenCodeEvolutionProposalDB

        db = SessionLocal()
        try:
            result = {
                "severity": "minor",
                "findings": [{"message": "test minor review path"}],
                "patches": [{"key": "max_daily_trades", "value": 11, "type": "tuning"}],
            }
            with patch("backend.config.settings.OPENCODE_AUTO_REVIEW", True):
                with patch(
                    "backend.services.opencode_proposal_reviewer.review_and_apply_proposal",
                    return_value={"status": "pending", "deferred": True},
                ) as mock_review:
                    out = route_analysis_result(db, result, window="24h", domain="ai")
                    assert out["proposal_id"] is not None
                    mock_review.assert_called_once()
            row = db.query(OpenCodeEvolutionProposalDB).filter(
                OpenCodeEvolutionProposalDB.id == out["proposal_id"]
            ).first()
            assert row.status == "pending"
        finally:
            db.close()


class TestOpenCodeAPIRoutes:
    def test_status_route_import(self):
        from backend.api.opencode_routes import opencode_status

        out = opencode_status()
        assert "bridge" in out
        assert "pace" in out

    def test_config_and_tuning_routes(self):
        from backend.api.opencode_routes import opencode_config, get_runtime_tuning, unlock_paper_pace

        cfg = opencode_config()
        assert "OPENCODE_ENABLED" in cfg
        assert "OPENCODE_AUTO_REVIEW" in cfg
        assert "note" in cfg

        tuning = get_runtime_tuning()
        assert "master_reduce_min_loss_pct" in tuning

        pace = unlock_paper_pace()
        assert "gear" in pace

    def test_report_content_rejects_traversal(self):
        from backend.api.opencode_routes import report_content
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            report_content(file="../secrets")
        assert exc.value.status_code == 400


class TestMasterCloseWithPolicy:
    def test_yaml_policy_blocks_tiny_loss_close(self):
        from backend.services.master_close_guard import check_master_close_hardfact

        r = check_master_close_hardfact(
            tier="mid", action="close",
            entry_price=100, mark_price=99, sl_price=90,
            unrealized_pnl=-1, margin=100, risk_score=40,
        )
        assert r.allow is False
