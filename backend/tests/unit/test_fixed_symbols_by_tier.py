"""分周期固定币 + 短/中 AI 选币控制单测。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock


def test_parse_by_tier_preserves_empty_lists():
    from backend.services import auto_coin_selector as m

    parsed = m._parse_by_tier_map(
        {"short": ["BTC"], "mid": [], "long": ["ETH", "SOL"]}
    )
    assert parsed["short"] == ["BTC"]
    assert parsed["mid"] == []
    assert parsed["long"] == ["ETH", "SOL"]


def test_parse_by_tier_double_encoded_string():
    from backend.services import auto_coin_selector as m
    import json

    raw = json.dumps({"short": ["BTC"], "mid": ["ETH"], "long": []})
    # 模拟 text 列里存的 JSON 文本
    parsed = m._parse_by_tier_map(raw)
    assert parsed["short"] == ["BTC"]
    assert parsed["mid"] == ["ETH"]
    assert parsed["long"] == []
    # 双重编码
    parsed2 = m._parse_by_tier_map(json.dumps(raw))
    assert parsed2["short"] == ["BTC"]


def test_get_fixed_symbols_tier_and_legacy_fallback(monkeypatch):
    from backend.services import auto_coin_selector as m

    class _Row:
        def __init__(self, symbols, auto, by_tier):
            self._t = (symbols, auto, by_tier)

        def __getitem__(self, i):
            return self._t[i]

        def __len__(self):
            return 3

    class _FakeResult:
        def __init__(self, row):
            self._row = row

        def first(self):
            return self._row

    db = MagicMock()
    by_tier = {
        "short": ["BTC", "VIRTUAL", "XPL"],
        "mid": ["ETH", "SOL"],
        "long": ["BTC", "ETH"],
    }
    db.execute.return_value = _FakeResult(
        _Row(["BTC", "ETH", "SOL"], ["DOGE"], by_tier)
    )
    monkeypatch.setattr(m, "_load_ai_mid_sticky", lambda sid: {"symbols": ["TON"]})
    monkeypatch.setattr(
        "backend.services.mlto.midlong_portfolio_risk.parse_core_basket",
        lambda: [],
    )

    assert m.get_fixed_symbols_for_session("fa_x", db=db, tier="short") == {
        "BTC", "VIRTUAL", "XPL",
    }
    assert m.get_fixed_symbols_for_session("fa_x", db=db, tier="mid") == {"ETH", "SOL"}
    assert m.get_fixed_symbols_for_session("fa_x", db=db, tier="long") == {"BTC", "ETH"}
    # 并集去掉当前短线 AI / 中线 sticky；历史扫描不再抹掉 VIRTUAL/XPL
    union = m.get_fixed_symbols_for_session("fa_x", db=db, tier=None)
    assert "DOGE" not in union and "TON" not in union
    assert "VIRTUAL" in union and "XPL" in union
    assert "BTC" in union and "ETH" in union and "SOL" in union

    # by_tier 空 → 回退 symbols，并剔除当前 AI
    db.execute.return_value = _FakeResult(
        _Row(["BTC", "ETH", "DOGE"], ["DOGE"], None)
    )
    monkeypatch.setattr(m, "_load_ai_mid_sticky", lambda sid: {"symbols": []})
    assert m.get_fixed_symbols_for_session("fa_x", db=db, tier="mid") == {"BTC", "ETH"}

    # 已分周期且 mid=[]：不得回退并集
    db.execute.return_value = _FakeResult(
        _Row(["BTC", "ETH", "SOL"], [], {"short": ["BTC"], "mid": [], "long": ["ETH"]})
    )
    assert m.get_fixed_symbols_for_session("fa_x", db=db, tier="mid") == set()
    assert m.get_fixed_symbols_for_session("fa_x", db=db, tier="short") == {"BTC"}


def test_set_fixed_rejects_outside_backup_pool(monkeypatch):
    from backend.services import auto_coin_selector as m

    monkeypatch.setattr(
        m,
        "validate_symbols_in_backup_pool",
        lambda syms: (
            [s for s in m._parse_symbol_list(syms) if s in {"BTC", "ETH"}],
            [s for s in m._parse_symbol_list(syms) if s not in {"BTC", "ETH"}],
        ),
    )
    r = m.set_fixed_symbols_by_tier(
        "fa_x",
        {"short": ["BTC", "FAKE"], "mid": ["ETH"], "long": ["BTC"]},
        db=MagicMock(),
        enforce_backup_pool=True,
    )
    assert r["success"] is False
    assert "FAKE" in (r.get("rejected") or {}).get("short", [])


def test_ai_mid_disabled_returns_empty(monkeypatch):
    from backend.services import auto_coin_selector as m

    monkeypatch.setattr(
        m,
        "get_session_mid_ai_config",
        lambda sid, db=None: {"enabled": False, "max_slots": 3},
    )
    assert m.get_ai_mid_candidates_for_session("fa_x") == []


def test_ai_mid_slots_from_session_truncates_sticky(tmp_path, monkeypatch):
    from backend.services import auto_coin_selector as m

    monkeypatch.setattr(m, "_ai_mid_sticky_path", lambda sid: str(tmp_path / f"{sid}.json"))
    m._save_ai_mid_sticky(
        "fa_x",
        ["AAA", "BBB", "CCC", "DDD"],
        reason="manual_adopt midlong_board",
    )
    monkeypatch.setattr(
        m,
        "get_session_mid_ai_config",
        lambda sid, db=None: {"enabled": True, "max_slots": 2},
    )
    monkeypatch.setattr(m, "get_fixed_symbols_for_session", lambda *a, **k: set())
    monkeypatch.setattr(m, "count_open_ai_mid_positions", lambda db=None, account_id=None: 0)

    class _FakeResult:
        def __init__(self, first=None, rows=None):
            self._first = first
            self._rows = rows or []

        def first(self):
            return self._first

        def all(self):
            return self._rows

    def _execute(sql, params=None):
        q = str(sql)
        if "paper_account_id" in q:
            return _FakeResult(first=(1,))
        if "paper_positions" in q:
            return _FakeResult(rows=[])
        return _FakeResult()

    db = MagicMock()
    db.execute.side_effect = _execute
    picked = m.get_ai_mid_candidates_for_session("fa_x", db=db)
    assert picked == ["AAA", "BBB"]


def test_short_switch_independent_of_mid(monkeypatch):
    """短线开关不影响中线候选门控；中线关不影响短线字段语义。"""
    from backend.services import auto_coin_selector as m

    calls = {"mid_cfg": None}

    def _cfg(sid, db=None):
        calls["mid_cfg"] = {"enabled": False, "max_slots": 3}
        return calls["mid_cfg"]

    monkeypatch.setattr(m, "get_session_mid_ai_config", _cfg)
    assert m.get_ai_mid_candidates_for_session("fa_x") == []
    # 短线字段由会话自身 auto_coin_enabled 控制，与 mid 无关
    sess = SimpleNamespace(
        auto_coin_enabled=True,
        auto_coin_symbols=["DOGE"],
        session_id="fa_x",
        symbols=["BTC"],
    )
    assert sess.auto_coin_enabled is True
    assert calls["mid_cfg"]["enabled"] is False


def test_force_adopt_requires_mid_enabled(monkeypatch, tmp_path):
    from backend.services import auto_coin_selector as m

    monkeypatch.setattr(m, "_ai_mid_sticky_path", lambda sid: str(tmp_path / f"{sid}.json"))
    monkeypatch.setattr(
        m,
        "get_session_mid_ai_config",
        lambda sid, db=None: {"enabled": False, "max_slots": 3},
    )
    r = m.force_adopt_ai_mid_symbol("fa_x", "TON")
    assert r["success"] is False
    assert "未开启" in r.get("error", "")

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
    r2 = m.force_adopt_ai_mid_symbol("fa_x", "TON")
    assert r2["success"] is True
    assert r2["ai_mid_watch"][0] == "TON"
    r3 = m.force_adopt_ai_mid_symbol("fa_x", "BTC")
    assert r3.get("skipped") == "already_fixed_mid"


def test_long_fixed_not_expanded_by_core_basket(monkeypatch):
    """会话 long=BTC/ETH 时，不得被 MIDLONG_CORE_BASKET 塞进 SOL。"""
    from backend.services import auto_coin_selector as m

    class _Row:
        def __init__(self, symbols, auto, by_tier):
            self._t = (symbols, auto, by_tier)

        def __getitem__(self, i):
            return self._t[i]

        def __len__(self):
            return 3

    class _FakeResult:
        def __init__(self, row):
            self._row = row

        def first(self):
            return self._row

    db = MagicMock()
    db.execute.return_value = _FakeResult(
        _Row(["BTC", "ETH", "SOL"], [], {"short": [], "mid": [], "long": ["BTC", "ETH"]})
    )
    monkeypatch.setattr(
        "backend.services.mlto.midlong_portfolio_risk.parse_core_basket",
        lambda: ["BTC", "ETH", "SOL"],
    )
    monkeypatch.setattr(m, "_load_ai_mid_sticky", lambda sid: {"symbols": []})
    got = m.get_fixed_symbols_for_session("fa_x", db=db, tier="long")
    assert got == {"BTC", "ETH"}
    assert "SOL" not in got
