# -*- coding: utf-8 -*-
"""v3 方向 6：job_registry / 统一告警 纯逻辑单测（不连数据库、不发真实告警）。"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest


def test_infer_interval_and_stale():
    from backend.services.ops.job_registry import _infer_interval, _is_stale

    assert _infer_interval("interval 60s") == 60
    assert _infer_interval("interval 5m") == 300
    assert _infer_interval("interval 2h") == 7200
    assert _infer_interval("daily 04:10") == 24 * 3600
    assert _infer_interval("weekly mon 04:00") == 7 * 24 * 3600
    assert _infer_interval("whatever") is None

    now = datetime.now(timezone.utc)
    assert _is_stale({"expected_interval_sec": None}, now) is None
    assert _is_stale({"expected_interval_sec": 60}, now) == "never_ran"
    fresh = (now - timedelta(seconds=30)).isoformat()
    assert _is_stale({"expected_interval_sec": 60, "last_end": fresh}, now) is None
    # 60s 任务：limit = max(120, 1860) = 1860s → 40 分钟滞后 warn，70 分钟中断 critical
    warn = (now - timedelta(minutes=40)).isoformat()
    assert _is_stale({"expected_interval_sec": 60, "heartbeat_at": warn}, now) == "warn"
    crit = (now - timedelta(minutes=70)).isoformat()
    assert _is_stale({"expected_interval_sec": 60, "last_end": crit}, now) == "critical"
    # 日任务：26h 才 warn
    day_ok = (now - timedelta(hours=25)).isoformat()
    assert _is_stale({"expected_interval_sec": 86400, "last_end": day_ok}, now) is None
    day_warn = (now - timedelta(hours=50)).isoformat()
    assert _is_stale({"expected_interval_sec": 86400, "last_end": day_warn}, now) == "warn"


def test_job_run_records_and_reraises(monkeypatch):
    from backend.services.ops import job_registry as jr

    events = []
    monkeypatch.setattr(jr, "ensure_schema", lambda: None)
    monkeypatch.setattr(jr, "is_enabled", lambda name: True)
    monkeypatch.setattr(jr, "_write_start", lambda name, s: events.append(("start", name)))
    monkeypatch.setattr(jr, "_write_end", lambda name, s, e, st, d, err, res: (events.append(("end", name, st, err)), 3)[1])
    alerts = []
    monkeypatch.setattr(jr, "_alert_failure", lambda name, exc, consec: alerts.append((name, consec)))

    with jr.job_run("demo") as rec:
        rec.set_result({"n": 1})
    assert events == [("start", "demo"), ("end", "demo", "ok", None)]

    events.clear()
    with pytest.raises(RuntimeError):
        with jr.job_run("demo"):
            raise RuntimeError("boom")
    assert events[-1][2] == "error" and "boom" in events[-1][3]
    assert alerts == [("demo", 3)]

    # 禁用 → 跳过
    monkeypatch.setattr(jr, "is_enabled", lambda name: False)
    with jr.job_run("demo") as rec:
        assert rec.skipped is True


def test_alert_dedupe_and_channels(monkeypatch):
    from backend.services.ops import alerts as al

    sent = {"feishu": [], "tg": [], "wh": []}
    monkeypatch.setattr(al, "_send_feishu", lambda lvl, t, x: (sent["feishu"].append(lvl), True)[1])
    monkeypatch.setattr(al, "_send_telegram", lambda lvl, t, x: (sent["tg"].append(lvl), True)[1])
    monkeypatch.setattr(al, "_send_webhooks", lambda p: (sent["wh"].append(p["level"]), 1)[1])
    al._dedupe.clear()

    r = al.send_alert("P0", "t", "x", dedupe_key="k1", async_send=False)
    assert r["sent"] is True
    r = al.send_alert("P0", "t", "x", dedupe_key="k1", async_send=False)
    assert r["deduped"] is True, "同 key 在窗口内应去重"
    # P2 默认只飞书
    al.send_alert("P2", "t", "x", dedupe_key="k2", async_send=False)
    assert sent["feishu"] == ["P0", "P2"]
    assert sent["tg"] == ["P0"] and sent["wh"] == ["P0"]
    # 非法级别归 P1
    r = al.send_alert("P9", "t", "x", async_send=False)
    assert r["level"] == "P1"
    st = al.alerts_status()
    assert st["stats"]["sent"] >= 3 and st["stats"]["deduped"] >= 1
