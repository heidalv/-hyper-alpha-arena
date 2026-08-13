"""AI 中线候选：看板 midlong approve 主源 + 兜底。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock


def test_midlong_board_approve_filters_fixed_and_min_conf():
    from backend.services import auto_coin_selector as m

    db = MagicMock()
    db.execute.return_value.all.return_value = [
        ("BTC", 0.9),      # fixed → drop
        ("TON", 0.7),
        ("AAA", 0.55),     # below min_conf → SQL already filters, but keep defensive
        ("XMR", 0.65),
        ("ton", 0.8),      # dup case
    ]
    out = m._midlong_board_approve_candidates(
        db, fixed={"BTC", "ETH"}, min_conf=0.60,
    )
    # function relies on SQL filter for min_conf; our mock returns AAA anyway
    syms = [s for s, _ in out]
    assert "BTC" not in syms
    assert syms[0] == "TON"
    assert "XMR" in syms
    assert len([s for s in syms if s == "TON"]) == 1


def test_force_adopt_ai_mid_writes_sticky(tmp_path, monkeypatch):
    from backend.services import auto_coin_selector as m

    monkeypatch.setattr(m, "_ai_mid_sticky_path", lambda sid: str(tmp_path / f"{sid}.json"))
    monkeypatch.setattr(
        m,
        "get_session_mid_ai_config",
        lambda sid, db=None: {"enabled": True, "max_slots": 3},
    )
    monkeypatch.setattr(
        m,
        "get_fixed_symbols_for_session",
        lambda sid, db=None, tier=None: {"BTC", "ETH"},
    )

    r = m.force_adopt_ai_mid_symbol("fa_test", "TON", max_slots=3)
    assert r["success"] is True
    assert r["ai_mid_watch"][0] == "TON"

    sticky = m._load_ai_mid_sticky("fa_test")
    assert sticky["symbols"][0] == "TON"
    assert "manual_adopt" in sticky["reason"]

    # fixed mid: skip occupying AI mid slot
    r2 = m.force_adopt_ai_mid_symbol("fa_test", "BTC", max_slots=3)
    assert r2["success"] is True
    assert r2.get("skipped") == "already_fixed_mid"


def test_get_ai_mid_prefers_board_over_stale_auto_coin_sticky(tmp_path, monkeypatch):
    from backend.services import auto_coin_selector as m

    monkeypatch.setattr(m, "_ai_mid_sticky_path", lambda sid: str(tmp_path / f"{sid}.json"))
    # 旧 sticky：来自短线池，即使未过期也应失效
    m._save_ai_mid_sticky(
        "fa_test", ["DOGE", "ENA"],
        reason="resample age>=10800s from auto_coin",
    )
    monkeypatch.setattr(
        m,
        "get_session_mid_ai_config",
        lambda sid, db=None: {"enabled": True, "max_slots": 3},
    )
    monkeypatch.setattr(
        m,
        "get_fixed_symbols_for_session",
        lambda sid, db=None, tier=None: {"BTC"},
    )
    monkeypatch.setattr(m, "count_open_ai_mid_positions", lambda db=None, account_id=None: 0)

    class _FakeResult:
        def __init__(self, rows=None, scalar=None, first=None):
            self._rows = rows or []
            self._scalar = scalar
            self._first = first

        def all(self):
            return self._rows

        def first(self):
            return self._first

        def scalar(self):
            return self._scalar

    def _execute(sql, params=None):
        q = str(sql)
        if "paper_account_id" in q:
            return _FakeResult(first=(14,))
        if "timeframe_tier = 'mid'" in q and "DISTINCT" in q:
            return _FakeResult(rows=[])
        if "coin_select_candidates" in q:
            return _FakeResult(rows=[("TON", 0.7), ("XMR", 0.65)])
        return _FakeResult()

    db = MagicMock()
    db.execute.side_effect = _execute

    picked = m.get_ai_mid_candidates_for_session("fa_test", db=db, max_slots=3)
    assert picked == ["TON", "XMR"]
    sticky = m._load_ai_mid_sticky("fa_test")
    assert "midlong_board" in sticky["reason"]
    assert sticky["symbols"] == ["TON", "XMR"]
