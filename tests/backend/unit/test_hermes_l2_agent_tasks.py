"""Hermes L2 — Agent task 质量评估单测。"""
import pytest


pytestmark = pytest.mark.unit


class TestHermesL2AgentTasks:
    def test_evaluate_agent_prompt_quality(self, tmp_path, monkeypatch):
        import backend.services.hermes_db as hdb
        from backend.services.hermes_prompt_optimizer_engine import prompt_optimizer

        db_path = tmp_path / "hermes_l2.db"
        monkeypatch.setattr(hdb, "HERMES_DB_PATH", str(db_path))
        hdb.init_hermes_db()

        hdb.hermes_execute(
            """INSERT INTO prompt_versions
               (task_id, version, full_text, change_type, status, created_at)
               VALUES (?,?,?,?,?,datetime('now'))""",
            ("task_swing_agent", "1.0.0", "prompt", "manual", "active"),
        )
        for outcome, pnl in [("win", 100), ("win", 50), ("loss", -30), ("loss", -20)]:
            hdb.hermes_execute(
                """INSERT INTO agent_decision_wisdom
                   (agent_type, symbol, side, outcome, pnl, created_at)
                   VALUES (?,?,?,?,?,datetime('now'))""",
                ("swing", "BTC", "long", outcome, pnl),
            )

        q = prompt_optimizer.evaluate_prompt_quality("task_swing_agent", "1.0.0")
        assert q["total"] == 4
        assert q["improved_rate"] == 0.5
        assert q["degraded_rate"] == 0.5
        assert q["avg_quality"] == 0.0

    def test_optimizable_tasks_include_agents(self):
        from backend.services.hermes_prompt_optimizer_engine import (
            AGENT_TASK_TYPES,
            OPTIMIZABLE_TASKS,
        )

        assert "task_swing_agent" in OPTIMIZABLE_TASKS
        assert "task_trend_agent_direction" in OPTIMIZABLE_TASKS
        assert AGENT_TASK_TYPES["task_swing_agent"] == "swing"
