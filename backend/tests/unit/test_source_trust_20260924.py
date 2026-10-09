# -*- coding: utf-8 -*-
"""[2026-09-24 新目标 R3] 信号源信任桥单测。

覆盖：开关关闭不生效 / 读不到文件 fail-open / 复盘过期不生效 / disable 生效 /
      scale_down 不改变行为 / **安全地板**（禁太多视为评估异常则不生效）/
      describe 可读 / brain 的 event_shock 消费点在源被禁后不再阻断。
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from backend.services.analysis import source_trust as ST


def _write_review(tmp_path, sources, age_h: float = 1.0):
    p = tmp_path / "latest_signal_review.json"
    ts_ms = int((dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=age_h)).timestamp() * 1000)
    p.write_text(json.dumps({
        "ts_ms": ts_ms,
        "findings": {"sources": [{"source": s, "verdict": v} for s, v in sources]},
    }, ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    ST._reset_cache_for_test()
    yield tmp_path
    ST._reset_cache_for_test()


def _point(monkeypatch, path):
    monkeypatch.setattr(ST, "_REVIEW_PATH", path)


def test_disabled_source_is_blocked(fresh, monkeypatch):
    _point(monkeypatch, _write_review(fresh, [("dual:event_impact", "disable"),
                                              ("dual:ok", "pass")]))
    assert ST.source_allowed("dual:event_impact") is False
    assert ST.source_allowed("dual:ok") is True


def test_scale_down_does_not_block(fresh, monkeypatch):
    """只认最强判据 disable；scale_down / observe 不改变行为。"""
    _point(monkeypatch, _write_review(fresh, [("dual:daily_brief", "scale_down"),
                                              ("e5_x", "observe")]))
    assert ST.source_allowed("dual:daily_brief") is True
    assert ST.source_allowed("e5_x") is True


def test_master_switch_off(fresh, monkeypatch):
    monkeypatch.setenv("SIGNAL_SOURCE_TRUST_ENABLED", "false")
    _point(monkeypatch, _write_review(fresh, [("dual:event_impact", "disable")]))
    assert ST.source_allowed("dual:event_impact") is True


def test_stale_review_ignored(fresh, monkeypatch):
    monkeypatch.setenv("SIGNAL_SOURCE_TRUST_MAX_AGE_H", "6")
    _point(monkeypatch, _write_review(fresh, [("dual:event_impact", "disable")], age_h=48))
    assert ST.source_allowed("dual:event_impact") is True, "过期复盘不得永久压制"


def test_safety_floor_aborts_all(fresh, monkeypatch):
    """禁掉比例 > 地板 ⇒ 视为评估异常，整体不生效。"""
    monkeypatch.setenv("SIGNAL_SOURCE_TRUST_MAX_DISABLE_FRAC", "0.5")
    # 4 个源、3 个 disable = 75% > 50% 且 n>=3（地板生效的最少源数）⇒ 应整体不生效
    _point(monkeypatch, _write_review(fresh, [("a", "disable"), ("b", "disable"),
                                              ("c", "disable"), ("d", "pass")]))
    assert ST.source_allowed("a") is True
    assert ST.source_allowed("b") is True


def test_missing_file_fails_open(fresh, monkeypatch):
    _point(monkeypatch, fresh / "nope.json")
    assert ST.source_allowed("dual:event_impact") is True


def test_describe_runs(fresh, monkeypatch):
    _point(monkeypatch, _write_review(fresh, [("dual:event_impact", "disable"),
                                              ("dual:ok", "pass")]))
    s = ST.describe()
    assert "dual:event_impact" in s and "enabled=" in s


def test_brain_event_shock_skipped_when_source_disabled(fresh, monkeypatch):
    """消费点验证：源被判 disable 后，brain 不再用它阻断开仓。"""
    _point(monkeypatch, _write_review(fresh, [("dual:event_impact", "disable"),
                                              ("dual:other", "pass")]))
    monkeypatch.setenv("MIDLONG_BRAIN_EVENT_SHOCK_SIGN_AWARE", "true")
    from backend.services.mlto.brain import ThesisDTO, thesis_watch_reason
    import backend.services.analysis.ledgers as L

    now = dt.datetime.now(dt.timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    monkeypatch.setattr(L, "list_signals",
                        lambda **kw: [{"created_ms": now_ms + 60_000, "direction": -1, "strength": 9.0}],
                        raising=False)
    dto = ThesisDTO(thesis_id="t", session_id="s", symbol="BTC", tier="mid", direction="long")
    for k, v in (("analysis_run_id", "run-x"), ("expires_at", now + dt.timedelta(hours=6)),
                 ("updated_at", now)):
        try:
            setattr(dto, k, v)
        except Exception:
            pass
    # 反向强冲击本应阻断；但该源已被判 disable ⇒ 不应阻断
    assert thesis_watch_reason(dto, "BTC", "mid", {}) != "event_shock"
