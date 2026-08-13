"""health-overview 聚合与 P0 告警节流 单元测试（R6-2/R6-3）"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def overview_mod():
    import backend.services.health_overview_service as mod
    return mod


def test_build_health_overview_aggregates_real_sources(overview_mod, monkeypatch):
    """聚合结构：各子源快照透传；子源失败不影响整体。"""
    monkeypatch.setattr(overview_mod, "_db_ping", lambda: {"ok": True, "latency_ms": 1.2})
    monkeypatch.setattr(overview_mod, "_errors_counts", lambda: {"ok": True, "counts": {"P0": 0, "P1": 1}})
    monkeypatch.setattr(overview_mod, "_learning_health", lambda: {"overall": "ok"})
    monkeypatch.setattr(overview_mod, "_funding_collector", lambda: {"has_report": True})
    monkeypatch.setattr(overview_mod, "_resources", lambda: {"disk_free_mb": 1.0})

    out = overview_mod.build_health_overview()
    assert set(out) == {"checked_at", "uptime_sec", "db", "errors", "learning_loops", "funding_collector", "resources"}
    assert out["db"]["ok"] is True
    assert out["errors"]["counts"] == {"P0": 0, "P1": 1}
    assert out["uptime_sec"] >= 0


def test_db_ping_failure_is_honest(overview_mod, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("db down")

    import backend.database.connection as conn
    monkeypatch.setattr(conn, "SessionLocal", boom)
    out = overview_mod._db_ping()
    assert out["ok"] is False
    assert "db down" in out["error"]


def test_p0_alert_throttle_same_count_once(monkeypatch):
    import backend.api.ops_routes as ops

    sent: list[str] = []

    class FakeNotifier:
        def send_sync(self, text, title="", *, level="info", event_type="system"):
            sent.append(title)
            return True

    monkeypatch.setenv("ALERT_P0_ENABLED", "true")
    monkeypatch.setattr("backend.services.openclaw_notify.get_notifier", lambda: FakeNotifier())
    ops._P0_ALERT_STATE.update({"last_sent": 0.0, "last_count": 0})

    ops._maybe_alert_p0(2)
    ops._maybe_alert_p0(2)  # 同计数 10 分钟内 → 节流
    assert len(sent) == 1
    assert "P0" in sent[0]

    ops._maybe_alert_p0(3)  # 计数变化 → 再发
    assert len(sent) == 2

    ops._maybe_alert_p0(0)  # 归零 → 复位不发
    assert len(sent) == 2
