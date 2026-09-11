"""OWM 断点回归：open_metadata / tag 落盘 / tier 归一。"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def test_normalize_owm_tier():
    from backend.services.mlto.learning_bridge import _normalize_owm_tier

    assert _normalize_owm_tier("swing") == "mid"
    assert _normalize_owm_tier("mid") == "mid"
    assert _normalize_owm_tier("trend_follow") == "long"
    assert _normalize_owm_tier("long") == "long"


def test_tag_position_force_saves(tmp_path, monkeypatch):
    from backend.services import source_attribution as sa

    state = tmp_path / "fusion_attribution.json"
    monkeypatch.setattr(sa, "_STATE_PATH", str(state))
    attr = sa.SourceAttribution()
    attr._loaded = True
    attr._tags = {}
    attr._last_save = 0
    attr.tag_position(999001, source="mlto", nature="swing", symbol="BTC", meta={"thesis_id": "t-1"})
    assert state.exists()
    raw = json.loads(state.read_text(encoding="utf-8"))
    assert raw["tags"]["999001"]["meta"]["thesis_id"] == "t-1"


def test_record_outcome_uses_session_and_mid_tier(monkeypatch):
    from backend.services.mlto import learning_bridge as lb

    class _NoRowAnalytics:
        """analytics 替身：`query(...).filter(...).limit(1).first()` **恒返回 None**。

        [§81 修复 2026-09-11 缺陷 #67] 原测试直接传 `MagicMock()`，而
        `_has_postmortem()` 的判据是 `row is not None` —— MagicMock 的链式调用返回的
        是 Mock（非 None），于是被判成"该 thesis 已有 postmortem"，`record_outcome`
        提前返回，测试报 `KeyError: 'session_id'`。**测试替身语义与真实 ORM 不符**，
        既制造假红、又会掩盖该去重闸的真实回归。
        """

        def query(self, *a, **k):
            return self

        def filter(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        def first(self):
            return None

    calls = {}

    def _fake_bump(db, session_id, tier, cited_ids, pnl, meta, analytics_db):
        calls["session_id"] = session_id
        calls["tier"] = tier
        calls["pnl"] = pnl

    monkeypatch.setattr(lb, "_bump_owm", _fake_bump)
    monkeypatch.setattr(
        "backend.services.mlto.thesis_store.append_event",
        lambda *a, **k: None,
        raising=False,
    )

    outcome = SimpleNamespace(
        pnl=12.0,
        tier="swing",
        exit_channel="tp",
        metadata={
            "thesis_id": "abc",
            "session_id": "fa_7e12e7a1b6",
            "timeframe_tier": "mid",
            "tier": "swing",
        },
    )
    lb.record_outcome(MagicMock(), outcome, analytics_db=_NoRowAnalytics())
    assert calls["session_id"] == "fa_7e12e7a1b6"
    assert calls["tier"] == "mid"
    assert calls["pnl"] == 12.0


def test_record_outcome_skips_when_postmortem_exists(monkeypatch):
    """[§81 缺陷 #67 配套] 去重闸的**正面契约**：已有 postmortem ⇒ 不得再次 bump OWM。

    与上一个用例互为反向（一个走"无记录"分支、一个走"已有记录"分支），
    两者合起来才算把 `_has_postmortem` 的判据真正锁住 —— 否则把判据改成恒 False
    （去重失效、postmortem 双写）也没有任何测试会红。
    """
    from backend.services.mlto import learning_bridge as lb

    class _HasRowAnalytics:
        def query(self, *a, **k):
            return self

        def filter(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        def first(self):
            return (1,)          # 已有 postmortem 行

    called = {"n": 0}

    def _fake_bump(*a, **k):
        called["n"] += 1

    monkeypatch.setattr(lb, "_bump_owm", _fake_bump)
    outcome = SimpleNamespace(
        pnl=12.0, tier="swing", exit_channel="tp",
        metadata={"thesis_id": "abc", "session_id": "fa_x", "timeframe_tier": "mid"},
    )
    lb.record_outcome(MagicMock(), outcome, analytics_db=_HasRowAnalytics())
    assert called["n"] == 0, "已有 postmortem 仍重复 bump OWM（去重闸失效）"
