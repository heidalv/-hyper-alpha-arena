"""2026-08-13 中长线修复（预算账户隔离/记忆过滤/ATR口径/结构位/因子措辞）回归测试。"""
from __future__ import annotations

import inspect

import pytest


# ──────────────────────────────────────────────────────────────────────
# P0-1 budget_service 账户隔离 + 未知 nature 排除
# ──────────────────────────────────────────────────────────────────────


class _FakePos:
    def __init__(self, account_id: int, nature: str, margin: float):
        self.account_id = account_id
        self.trade_nature = nature
        self.margin = margin


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows
        self.filters = []

    def filter(self, *filters):
        self.filters.extend(filters)
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows

    def query(self, *args):
        return _FakeQuery(self.rows)

    def close(self):
        pass


def test_nature_to_layer_unknown_returns_none():
    from backend.services.budget_service import budget_service
    assert budget_service.nature_to_layer("swing") == "trend"
    assert budget_service.nature_to_layer("trend_follow") == "trend"
    assert budget_service.nature_to_layer("scalp") == "scalp"
    assert budget_service.nature_to_layer("pair_research") is None
    assert budget_service.nature_to_layer("research") is None


def test_used_margin_scoped_to_account_and_excludes_unknown_nature(monkeypatch):
    import backend.database.connection as conn
    from backend.services.budget_service import budget_service

    rows = [
        _FakePos(14, "swing", 100.0),
        _FakePos(149, "pair_research", 9000.0),
        _FakePos(14, "pair_research", 50.0),
        _FakePos(14, "scalp", 20.0),
        _FakePos(149, "trend_follow", 300.0),
    ]
    monkeypatch.setattr(conn, "SessionLocal", lambda: _FakeSession(rows))

    # 账户 14 的 trend 层只有 swing(100)；pair_research 被排除、scalp 属另一层
    assert budget_service.get_used_margin("trend", account_id=14) == pytest.approx(100.0)
    # 全局聚合也排除未知 nature，且不跨层
    assert budget_service.get_used_margin("trend") == pytest.approx(400.0)


def test_scale_factor_passes_account_id(monkeypatch):
    from backend.services.budget_service import BudgetService, budget_service

    seen: dict = {}

    def fake_used(layer, mode="paper", account_id=None):
        seen["account_id"] = account_id
        return 0.0

    monkeypatch.setattr(budget_service, "get_used_margin", fake_used)
    monkeypatch.setattr(
        BudgetService,
        "layer_allocations",
        property(lambda self: {"scalp": 0.35, "trend": 0.65}),
    )
    factor = budget_service.scale_factor_for_layer(
        "mid", 1000.0, "paper", account_id=14
    )
    assert factor == pytest.approx(1.0)
    assert seen.get("account_id") == 14


def test_scale_factor_unknown_tier_short_circuits(monkeypatch):
    from backend.services.budget_service import BudgetService, budget_service

    called = {"n": 0}

    def fake_used(layer, mode="paper", account_id=None):
        called["n"] += 1
        return 0.0

    monkeypatch.setattr(budget_service, "get_used_margin", fake_used)
    monkeypatch.setattr(
        BudgetService,
        "layer_allocations",
        property(lambda self: {"scalp": 0.35, "trend": 0.65}),
    )
    assert budget_service.scale_factor_for_layer("pair_research", 1000.0) == 1.0
    assert called["n"] == 0


# ──────────────────────────────────────────────────────────────────────
# P1 agent_quant_feature_table：记忆账户/nature 过滤 + ATR 口径
# ──────────────────────────────────────────────────────────────────────


class _Col:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return (self.name, "__eq__", other)

    def in_(self, other):
        return (self.name, "__in__", other)

    def __ge__(self, other):
        return (self.name, "__ge__", other)


class _PP:
    symbol = _Col("symbol")
    status = _Col("status")
    closed_at = _Col("closed_at")
    opened_at = _Col("opened_at")
    account_id = _Col("account_id")
    trade_nature = _Col("trade_nature")


class _CapturingSession:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.filters = []

    def query(self, *args):
        q = self

        class _Q:
            def filter(_s, *filters):
                q.filters.extend(filters)
                return _s

            def order_by(_s, *args):
                return _s

            def limit(_s, *args):
                return _s

            def all(_s):
                return q.rows

            def count(_s):
                return len(q.rows)

        return _Q()

    def close(self):
        pass


def test_trade_memory_filters_account_and_nature(monkeypatch):
    import backend.database.models as models
    from backend.services.agent_quant_feature_table import _get_trade_memory

    sess = _CapturingSession()
    monkeypatch.setattr(models, "PaperPosition", _PP)
    # 隔离 reentry_cooldown 状态查询，避免环境依赖
    monkeypatch.setattr(
        "backend.services.agent_quant_feature_table._get_cooldown_status",
        lambda account_id, symbol: (0, []),
    )
    res = _get_trade_memory(sess, "BTC", 14, limit=5, window_days=14, nature="swing")
    flat = sess.filters
    assert ("account_id", "__eq__", 14) in flat
    assert ("trade_nature", "__in__", ("swing",)) in flat
    assert res["recent_trades"] == []


def test_count_opens_filters_account(monkeypatch):
    import backend.database.models as models
    from backend.services.agent_quant_feature_table import _count_opens_today

    sess = _CapturingSession()
    monkeypatch.setattr(models, "PaperPosition", _PP)
    _count_opens_today(sess, "BTC", 14, scope=("trend_follow", "position"))
    assert ("account_id", "__eq__", 14) in sess.filters
    assert ("trade_nature", "__in__", ("trend_follow", "position")) in sess.filters


def test_resolve_atr_pct_explicit_field(monkeypatch):
    from backend.services.agent_quant_feature_table import _resolve_atr_pct
    ms = {"current_price": 100.0, "atr_1d_pct": 0.0134}
    pct, abs_ = _resolve_atr_pct(ms, "BTC", "1d")
    assert pct == pytest.approx(0.0134)
    assert abs_ == pytest.approx(1.34)


def test_resolve_atr_pct_indicator_absolute(monkeypatch):
    from backend.services.agent_quant_feature_table import _resolve_atr_pct
    ms = {"current_price": 100.0, "indicators_1d": {"atr": 1.34}}
    pct, abs_ = _resolve_atr_pct(ms, "BTC", "1d")
    assert pct == pytest.approx(0.0134)
    assert abs_ == pytest.approx(1.34)


def test_resolve_atr_pct_kline_fallback(monkeypatch):
    from backend.services.agent_quant_feature_table import _resolve_atr_pct
    rows = [
        {"high": 103.0, "low": 97.0, "close": 100.0}
        for _ in range(20)
    ]
    ms = {"current_price": 100.0, "indicators_1d": {"recent_klines": rows}}
    pct, abs_ = _resolve_atr_pct(ms, "BTC", "1d")
    assert pct > 0
    assert abs_ == pytest.approx(pct * 100.0)


# ──────────────────────────────────────────────────────────────────────
# P2 quant_brief：结构位渲染 + 因子快照三种状态
# ──────────────────────────────────────────────────────────────────────


def test_structure_section_renders_levels():
    from backend.services.decision_core.quant_brief import _structure_section
    ms = {
        "current_price": 100.0,
        "structure_levels": {"support": 95.0, "resistance": 110.0},
    }
    text = _structure_section(ms)
    assert "支撑95" in text
    assert "阻力110" in text


def test_missing_section_factor_three_states():
    from backend.services.decision_core.quant_brief import _missing_section
    base = {
        "indicators_1h": {"rsi": 50},
        "indicators_4h": {"rsi": 50},
        "indicators_1d": {"rsi": 50},
    }
    assert "活跃因子：无快照" in _missing_section(dict(base))
    assert "活跃因子：0 个" in _missing_section(
        {**base, "midlong_factors": {"count": 0}}
    )
    assert "活跃因子：3 个" in _missing_section(
        {**base, "midlong_factors": {"count": 3}}
    )


# ──────────────────────────────────────────────────────────────────────
# [2026-08-17] test_execute_mlto_lane_passes_through_tier 已删：
# execute_mlto_lane 函数已删除（旧长线 MLTO lane LLM 下线）。
# ──────────────────────────────────────────────────────────────────────
