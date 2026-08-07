"""窄训练期 / TrainingOrchestrator — 单元与集成测试。"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def isolated_training_state(tmp_path, monkeypatch):
    """隔离 training_phase.json 与 overlay 目录。"""
    state_file = tmp_path / "training_phase.json"
    audit_file = tmp_path / "training_audit.jsonl"
    live_audit = tmp_path / "training_live_audit.jsonl"
    overlay_dir = tmp_path / "overlays"
    overlay_dir.mkdir()

    import backend.services.training_phase_service as tps
    import backend.services.training_audit as ta
    import backend.services.runtime_tuning_store as rts

    monkeypatch.setattr(tps, "STATE_FILE", str(state_file))
    monkeypatch.setattr(tps, "_cache", {"ts": 0.0, "data": {}})
    monkeypatch.setattr(ta, "TRAINING_AUDIT_FILE", str(audit_file))
    monkeypatch.setattr(ta, "LIVE_AUDIT_FILE", str(live_audit))
    monkeypatch.setattr(ta, "REPORT_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(rts, "OVERLAY_DIR", str(overlay_dir))

    tps.save_state({
        "active": True,
        "symbols": ["BTC", "ETH", "SOL"],
        "max_active_strategies": 10,
        "graduation_queue": [],
        "strategy_graduation": {},
        "champion_windows": {},
    })
    yield {"state_file": state_file, "audit_file": audit_file, "overlay_dir": overlay_dir}


class TestTrainingPhaseService:
    def test_defaults_and_active(self, isolated_training_state):
        from backend.services.training_phase_service import (
            is_active,
            target_symbols,
            min_analysis_closed,
            max_active_strategies,
        )

        assert is_active() is True
        assert target_symbols() == ["BTC", "ETH", "SOL"]
        assert min_analysis_closed() == 3
        assert max_active_strategies() == 10

    def test_graduation_status_roundtrip(self, isolated_training_state):
        from backend.services.training_phase_service import (
            set_graduation_status,
            get_graduation_status,
            enqueue_graduation,
            load_state,
        )

        set_graduation_status("strat_001", "graduated", trades=25)
        assert get_graduation_status("strat_001") == "graduated"
        enqueue_graduation("strat_001")
        assert "strat_001" in load_state().get("graduation_queue", [])


class TestProposalValidationPolicyTraining:
    def test_training_narrow_profile_when_active(self, isolated_training_state):
        from backend.services.proposal_validation_policy import (
            validation_policy_for_gear,
            min_eval_samples,
            can_evaluate_proposal,
        )

        pol = validation_policy_for_gear()
        assert pol.get("gear") == "training_narrow"
        assert pol.get("min_age_hours") == 2
        assert min_eval_samples() == 3
        ready, reason = can_evaluate_proposal(
            age_hours=3.0, post_apply_closed=3, gear="turbo"
        )
        assert ready is True
        assert reason == "ready"

    def test_inactive_uses_default_samples(self, isolated_training_state, monkeypatch):
        import backend.services.training_phase_service as tps

        state = tps.load_state()
        state["active"] = False
        tps.save_state(state)
        tps._cache["ts"] = 0.0

        from backend.services.proposal_validation_policy import min_eval_samples

        assert min_eval_samples(force=False) == 5


class TestRuntimeTuningOverlay:
    def test_overlay_save_merge_remove(self, isolated_training_state):
        from backend.services import runtime_tuning_store as rts

        path = rts.save_overlay(42, {"max_daily_trades": 10})
        assert os.path.isfile(path)
        merged = rts.merge_overlay_to_global(42)
        assert merged.get("merged") is True
        assert rts.remove_overlay(42) is True
        assert not os.path.isfile(os.path.join(rts.OVERLAY_DIR, "42.json"))


class TestTrainingAudit:
    def test_audit_jsonl_append(self, isolated_training_state):
        from backend.services.training_audit import log_training_event, log_live_event

        log_training_event("test_event", foo="bar")
        log_live_event("promote_l0", strategy_id="s1")
        audit_path = isolated_training_state["audit_file"]
        assert audit_path.exists()
        lines = audit_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["event"] == "test_event"
        assert rec["foo"] == "bar"


class TestOpenCodeContextPackTraining:
    def test_training_phase_in_pack(self, isolated_training_state):
        from backend.database.connection import SessionLocal
        from backend.services.opencode_context_pack import build_context_pack

        db = SessionLocal()
        try:
            pack = build_context_pack(db, window="24h", domain="ai")
            assert "training_phase" in pack
            assert pack["training_phase"].get("active") is True
            assert pack["training_phase"].get("symbols") == ["BTC", "ETH", "SOL"]
            assert "health_apis_snapshot" in pack
            dq = pack["data_quality"]
            closed = int(pack["runtime_report"].get("total_closed") or 0)
            if closed >= 3:
                assert dq["sufficient_for_analysis"] is True
            else:
                assert dq["sufficient_for_analysis"] is (closed >= 3)
        finally:
            db.close()


class TestOpenCodeBridgeTrainingThreshold:
    def test_skip_message_uses_training_min(self, isolated_training_state):
        from backend.services.opencode_bridge import run_scheduled_analysis

        fake_pack = {
            "data_quality": {
                "runtime_report_total_closed": 2,
                "sufficient_for_analysis": False,
            },
        }
        with patch("backend.services.opencode_context_pack.build_context_pack", return_value=fake_pack), \
             patch("backend.services.opencode_context_pack.save_context_pack", return_value="/tmp/ctx.json"):
            out = run_scheduled_analysis(MagicMock(), window="24h", domain="ai")
            assert out.get("skipped") == "insufficient_trade_data"
            assert ">=3" in out.get("message", "")


class TestProposalApplierTraining:
    def test_auto_promoted_skips_manual_confirm(self):
        from backend.database.connection import SessionLocal
        from backend.database.models import OpenCodeEvolutionProposalDB
        from backend.services.opencode_proposal_applier import create_proposal, apply_proposal

        db = SessionLocal()
        try:
            pid = create_proposal(
                db,
                [{"key": "max_daily_trades", "value": 9, "type": "tuning"}],
                severity="major",
                title="auto promote test",
                dedupe_key=f"test_auto_promote_{uuid.uuid4().hex}",
            )
            assert pid is not None
            with patch("backend.services.opencode_proposal_applier._apply_tuning_and_policy") as mock_apply:
                mock_apply.return_value = {"tuning": {"max_daily_trades": 9}, "policy": []}
                out = apply_proposal(db, pid, to_live=True, auto_promoted=True)
            assert out["apply_mode"] == "live"
            row = db.query(OpenCodeEvolutionProposalDB).filter(
                OpenCodeEvolutionProposalDB.id == pid
            ).first()
            assert row.status == "applied"
        finally:
            db.close()

    def test_dedupe_blocks_recent_rolled_back(self):
        from backend.database.connection import SessionLocal
        from backend.database.models import OpenCodeEvolutionProposalDB
        from backend.services.opencode_proposal_applier import create_proposal

        db = SessionLocal()
        dedupe = f"dedupe_rolled_back_{uuid.uuid4().hex}"
        try:
            row = OpenCodeEvolutionProposalDB(
                source="test",
                severity="minor",
                title="old rolled",
                proposal_json=json.dumps({"patches": [], "dedupe_key": dedupe}),
                patch_type="tuning",
                status="rolled_back",
                requires_paper_validation=True,
                requires_manual_live_confirm=False,
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            db.add(row)
            from backend.database.connection import sqlite_write_commit
            sqlite_write_commit(db)

            pid = create_proposal(
                db,
                [{"key": "max_daily_trades", "value": 8, "type": "tuning"}],
                dedupe_key=dedupe,
                title="dup after rollback",
            )
            assert pid == row.id
        finally:
            db.close()


class TestProposalReviewerTrainingMajor:
    def test_training_auto_apply_major(self, isolated_training_state):
        from backend.database.connection import SessionLocal
        from backend.database.models import OpenCodeEvolutionProposalDB
        from backend.services.opencode_proposal_applier import create_proposal
        from backend.services.opencode_proposal_reviewer import review_and_apply_proposal

        db = SessionLocal()
        try:
            patches = [{"key": "max_daily_trades", "value": 10, "type": "tuning"}]
            pid = create_proposal(
                db,
                patches,
                severity="major",
                title="major auto training",
                dedupe_key=f"major_auto_training_{uuid.uuid4().hex}",
            )
            assert pid is not None
            row = db.query(OpenCodeEvolutionProposalDB).filter(
                OpenCodeEvolutionProposalDB.id == pid
            ).first()
            assert row.status == "pending"
            with patch(
                "backend.services.opencode_proposal_reviewer.run_review_agent",
            ) as mock_llm:
                out = review_and_apply_proposal(db, pid)
                mock_llm.assert_not_called()
            assert out.get("review", {}).get("source") == "training_orchestrator"
            db.refresh(row)
            assert row.status == "paper_applying"
        finally:
            db.close()


class TestTrainingOrchestrator:
    def test_validated_merge_logs_audit(self, isolated_training_state):
        from backend.database.connection import SessionLocal
        from backend.database.models import OpenCodeEvolutionProposalDB
        from backend.services.training_orchestrator import run_validated_merge
        from backend.services.runtime_tuning_store import save_overlay

        db = SessionLocal()
        try:
            row = OpenCodeEvolutionProposalDB(
                source="test",
                severity="minor",
                title="validated merge",
                proposal_json="{}",
                patch_type="tuning",
                status="paper_validated",
                after_json=json.dumps({"verdict": "improved"}),
                applied_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            db.add(row)
            from backend.database.connection import sqlite_write_commit
            sqlite_write_commit(db)
            save_overlay(row.id, {"max_daily_trades": 11})

            out = run_validated_merge(db)
            assert out.get("merged", 0) >= 1
            audit = isolated_training_state["audit_file"].read_text(encoding="utf-8")
            assert "validated_merge" in audit
        finally:
            db.close()

    def test_register_training_jobs_no_crash(self):
        from backend.services.training_orchestrator import (
            JOB_REBALANCE,
            JOB_GRADUATION,
            JOB_CHAMPION,
        )

        assert JOB_REBALANCE == "training_portfolio_rebalance"
        assert JOB_GRADUATION == "training_graduation_scan"
        assert JOB_CHAMPION == "training_champion_recovery"

        mock_scheduler = MagicMock()
        mock_scheduler.is_running.return_value = True
        mock_scheduler.scheduler = MagicMock()
        mock_scheduler.scheduler.get_job.return_value = None

        with patch("backend.services.scheduler.task_scheduler", mock_scheduler), \
             patch("backend.services.training_orchestrator.boot_training_phase", return_value={"booted": True}):
            from backend.services.training_orchestrator import register_training_jobs
            register_training_jobs()
        assert mock_scheduler.add_interval_task.call_count >= 8


class TestTrainingAPIRoutes:
    def test_training_phase_status_route(self, isolated_training_state):
        from backend.api.training_phase_routes import training_phase_status
        from backend.database.connection import SessionLocal

        db = SessionLocal()
        try:
            out = training_phase_status(db=db)
            assert out["active"] is True
            assert "funnel" in out
            assert "symbols" in out
        finally:
            db.close()

    def test_proposal_funnel_enhanced_fields(self):
        from backend.api.opencode_routes import proposal_funnel
        from backend.database.connection import SessionLocal

        db = SessionLocal()
        try:
            out = proposal_funnel(db=db)
            assert "funnel" in out
            assert "improve_rate" in out
            assert "rollback_rate" in out
            assert "inconclusive_reasons" in out
            assert "paper_applying" in out
        finally:
            db.close()


class TestPaperPaceEvalInterval:
    def test_reads_env_interval(self, monkeypatch):
        monkeypatch.setenv("PAPER_PACE_EVAL_INTERVAL_S", "900")
        from backend.services.paper_pace_controller import PaperPaceController

        ctrl = PaperPaceController()
        ctrl._initialized = False
        ctrl.__init__()
        assert ctrl._eval_interval == 900


class TestSettingsTrainingFlags:
    def test_training_defaults(self):
        from backend.config.settings import (
            TRAINING_PHASE_AUTO,
            TRAINING_AUTO_LIVE,
            TRAINING_AUTO_APPLY_MAJOR,
            NSGA2_ENABLED,
            PAPER_PACE_EVAL_INTERVAL_S,
        )

        assert isinstance(TRAINING_PHASE_AUTO, bool)
        assert isinstance(TRAINING_AUTO_LIVE, bool)
        assert isinstance(TRAINING_AUTO_APPLY_MAJOR, bool)
        assert isinstance(NSGA2_ENABLED, bool)
        assert PAPER_PACE_EVAL_INTERVAL_S > 0


class TestEvolutionSchedulerNSGA2:
    def test_weekly_skipped_when_disabled(self):
        from backend.services.evolution_scheduler import EvolutionScheduler

        sched = EvolutionScheduler()
        sched._running_evolution = False
        with patch("backend.config.settings.NSGA2_ENABLED", False):
            sched.weekly_evolution()
        assert sched._running_evolution is False
