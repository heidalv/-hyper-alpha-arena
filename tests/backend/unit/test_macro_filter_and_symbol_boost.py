"""M3: 宏观过滤接入 V5 RegimeAgent + symbol boost 动态刷新 单元测试"""

import time

from backend.services.rebate_arb import macro_direction_filter as mdf
from backend.services.rebate_arb import symbol_boost_store as sbs
from backend.services.rebate_arb.strategies.s8_asterdex_rh import S8AsterdexRhStrategy
from backend.services.decision_core.regime_agent import RegimeResult


def test_macro_filter_skips_on_extreme_regime(monkeypatch):
    """RegimeAgent 极端态 → 禁止开仓（与 V5 unified_gate 行为一致）。"""
    monkeypatch.setattr(
        mdf, "_classify_symbol_regime",
        lambda symbol: RegimeResult("extreme", 100, False, "24h=+15.0%"),
    )
    result = mdf.evaluate_macro_filter("BTC", "bullish")
    assert result["action"] == "skip"
    assert result["passed"] is False
    assert result["regime"] == "extreme"
    assert "regime_extreme" in result["reason"]


def test_macro_filter_ranging_applies_confidence_penalty(monkeypatch):
    """震荡市轻度降置信但不阻断（mt_orchestrator 不可用时仍 fail-closed）。"""
    monkeypatch.setattr(
        mdf, "_classify_symbol_regime",
        lambda symbol: RegimeResult("ranging", 10, True, "24h=+1.0%"),
    )

    class _View:
        bias = "bullish"
        signal = "bullish"
        confidence = 0.6

    class _Decision:
        long_view = _View()
        mid_view = _View()
        allowed_direction = "both"

    class _FakeOrchestrator:
        def evaluate(self, symbol):
            return _Decision()

    import backend.services.multi_timeframe_orchestrator as mto

    monkeypatch.setattr(mto, "mt_orchestrator", _FakeOrchestrator())

    result = mdf.evaluate_macro_filter("BTC", "bullish")
    assert result["action"] == "allow"
    assert result["regime"] == "ranging"
    # ranging 惩罚 -0.05 与同向加成 +0.10 累加 = +0.05
    assert abs(result["confidence_adjust"] - 0.05) < 1e-9


def test_macro_filter_regime_unavailable_does_not_block(monkeypatch):
    """RegimeAgent 数据缺失（None）时不阻断，主过滤层逻辑照常。"""
    monkeypatch.setattr(mdf, "_classify_symbol_regime", lambda symbol: None)

    class _View:
        bias = "bullish"
        signal = "bullish"
        confidence = 0.6

    class _Decision:
        long_view = _View()
        mid_view = _View()
        allowed_direction = "both"

    class _FakeOrchestrator:
        def evaluate(self, symbol):
            return _Decision()

    import backend.services.multi_timeframe_orchestrator as mto

    monkeypatch.setattr(mto, "mt_orchestrator", _FakeOrchestrator())

    result = mdf.evaluate_macro_filter("BTC", "bullish")
    assert result["action"] == "allow"
    assert result["regime"] == "unknown"


def test_symbol_boost_store_update_and_ttl(monkeypatch):
    """boost map 更新后可读取；过期后返回 None。"""
    assert sbs.update_symbol_boost_map({"btc/usdt": 1.8, "ASTER/USDT": 2.5}, source="test")
    runtime = sbs.get_runtime_symbol_boost_map()
    assert runtime == {"BTC/USDT": 1.8, "ASTER/USDT": 2.5}

    status = sbs.get_symbol_boost_status()
    assert status["source"] == "test"
    assert status["stale"] is False

    # 模拟过期
    with sbs._lock:
        sbs._state["updated_at"] = time.time() - sbs.DEFAULT_TTL_SECONDS - 1
    assert sbs.get_runtime_symbol_boost_map() is None

    # 清理，避免污染其他测试
    with sbs._lock:
        sbs._state["map"] = {}
        sbs._state["updated_at"] = 0.0


def test_symbol_boost_store_rejects_invalid():
    assert sbs.update_symbol_boost_map({}) is False
    assert sbs.update_symbol_boost_map(None) is False
    assert sbs.update_symbol_boost_map({"BTC/USDT": "abc"}) is False


def test_s8_close_dispatches_learning_outcome(monkeypatch):
    """M5/L2: S8 平仓后把方向/盈亏写入统一学习闭环。

    L2 收敛后引擎不再直接调 learning_bus.dispatch —— 统一交给
    unified_learning.process_outcome 内部调度全部学习后端。本测试因此只校验
    process_outcome 被调用一次且 outcome 字段正确。
    """
    import backend.database.connection as conn_mod
    import backend.services.unified_learning_service as uls
    from backend.services.rebate_arb.engine import rebate_arb_engine
    from backend.services.rebate_arb.models import (
        RebatePosition, RebatePositionStatus, RebateStrategyType,
    )

    captured = {}

    class _FakeDB:
        def close(self):
            pass

    monkeypatch.setattr(conn_mod, "SessionLocal", lambda: _FakeDB())
    monkeypatch.setattr(
        uls.unified_learning, "process_outcome",
        lambda db, outcome: captured.__setitem__("uls_outcome", outcome),
    )

    pos = RebatePosition(
        position_id="s8-learn-test",
        strategy_type=RebateStrategyType.S8_ASTERDEX_RH,
        source_exchange="asterdex",
        target_exchange=None,
        symbol="BTC/USDT",
        side_a_size=600.0,
        entry_time=time.time() - 3600,
        paper_mode=True,
        status=RebatePositionStatus.CLOSED,
        current_pnl=1.25,
        accumulated_rebate=0.1,
        accumulated_points=42.0,
        metadata={
            "margin_usd": 60.0,
            "side_a": {"side": "sell"},
            "ai_signal": {"ai_direction": "bearish", "ai_confidence": 72},
            "rh_optimization_mode": "stage6_optimal",
        },
    )

    rebate_arb_engine._dispatch_learning_outcome(pos, "hold_complete")

    # process_outcome 被调用（统一学习闭环入口）
    assert "uls_outcome" in captured
    outcome = captured["uls_outcome"]
    assert outcome.strategy_id == "rebate_S8"
    assert outcome.side == "sell"
    assert outcome.pnl == 1.25
    assert outcome.source == "paper"
    assert outcome.metadata["paper_position_id"] == "s8-learn-test"
    assert outcome.metadata["points"] == 42.0


def test_s5_close_does_not_dispatch_learning(monkeypatch):
    """非方向型/已下线策略平仓不进学习闭环。"""
    import backend.database.connection as conn_mod
    from backend.services.rebate_arb.engine import rebate_arb_engine
    from backend.services.rebate_arb.models import (
        RebatePosition, RebatePositionStatus, RebateStrategyType,
    )

    called = {"db": False}
    monkeypatch.setattr(
        conn_mod, "SessionLocal",
        lambda: called.__setitem__("db", True),
    )

    pos = RebatePosition(
        position_id="s5-no-learn",
        strategy_type=RebateStrategyType.S5_FUNDING_POINTS,
        source_exchange="hyperliquid",
        target_exchange=None,
        symbol="ETH/USDT",
        side_a_size=100.0,
        paper_mode=True,
        status=RebatePositionStatus.CLOSED,
    )
    rebate_arb_engine._dispatch_learning_outcome(pos, "manual")
    assert called["db"] is False


def test_s8_symbol_boost_prefers_runtime_map():
    """S8 优先读运行时动态 boost，回退静态 map。"""
    s8 = S8AsterdexRhStrategy()
    # 静态 map：BTC 1.5
    assert s8.symbol_boost("BTC/USDT") == 1.5

    sbs.update_symbol_boost_map({"BTC/USDT": 3.0}, source="test_runtime")
    try:
        assert s8.symbol_boost("BTC/USDT") == 3.0
        # 运行时 map 中没有的币种 → 1.0（运行时 map 整体取代静态 map）
        assert s8.symbol_boost("DOGE/USDT") == 1.0
    finally:
        with sbs._lock:
            sbs._state["map"] = {}
            sbs._state["updated_at"] = 0.0

    # 清空后回退静态 map
    assert s8.symbol_boost("BTC/USDT") == 1.5
