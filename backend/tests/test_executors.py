"""统一执行层接口测试（阶段 3）。

验证:
- OrderContext / OrderResult 数据类契约
- PaperExecutor 返回值标准化（异构 paper_engine 返回 → 统一 OrderResult）
- LiveExecutor 决策构造 + 触发
- get_executor 工厂路由
- USE_UNIFIED_EXECUTOR 开关
"""
import os
import pytest
from unittest.mock import MagicMock, patch

from backend.services.exchange.executors import (
    ExecutionChannel,
    OrderContext,
    OrderResult,
    get_executor,
    is_unified_executor_enabled,
)
from backend.services.exchange.paper_executor import PaperExecutor
from backend.services.exchange.live_executor import LiveExecutor


# ── OrderContext ────────────────────────────────────────────────

def test_order_context_basic():
    ctx = OrderContext(
        account_id=1, symbol="BTC", side="buy", quantity=0.1,
        leverage=5.0, tp_price=65000, sl_price=55000,
    )
    assert ctx.account_id == 1
    assert ctx.symbol == "BTC"
    assert ctx.side == "buy"
    assert ctx.quantity == 0.1
    assert ctx.order_type == "market"
    assert ctx.leverage == 5.0
    assert ctx.tp_price == 65000
    assert ctx.sl_price == 55000
    assert ctx.reduce_only is False


def test_order_context_to_paper_kwargs():
    ctx = OrderContext(
        account_id=1, symbol="BTC", side="buy", quantity=0.1,
        order_type="limit", price=60000, leverage=5.0,
        tp_price=65000, sl_price=55000,
        strategy_id="strat_1", timeframe_tier="mid",
        trade_nature="swing", expected_hold_hours=8.0,
    )
    kwargs = ctx.to_paper_kwargs()
    # account_id/symbol/side/quantity 不在 kwargs 中（它们是 place_order 的位置参数）
    assert "account_id" not in kwargs
    assert "symbol" not in kwargs
    assert "side" not in kwargs
    assert "quantity" not in kwargs
    assert kwargs["order_type"] == "limit"
    assert kwargs["price"] == 60000
    assert kwargs["leverage"] == 5.0
    assert kwargs["tp_price"] == 65000
    assert kwargs["sl_price"] == 55000
    assert kwargs["strategy_id"] == "strat_1"
    assert kwargs["timeframe_tier"] == "mid"
    assert kwargs["trade_nature"] == "swing"
    assert kwargs["expected_hold_hours"] == 8.0


# ── OrderResult ─────────────────────────────────────────────────

def test_order_result_success_filled():
    r = OrderResult(status="filled", order_id="o1", position_id=10, fill_price=60000)
    assert r.success is True
    assert r.is_blocked is False


def test_order_result_success_pending():
    r = OrderResult(status="pending")
    assert r.success is True


def test_order_result_rejected_not_success():
    r = OrderResult(status="rejected", error="余额不足")
    assert r.success is False
    assert r.is_blocked is False


def test_order_result_blocked():
    r = OrderResult(status="blocked", blocked_by="max_daily_trades")
    assert r.success is False
    assert r.is_blocked is True
    assert r.blocked_by == "max_daily_trades"


def test_order_result_to_dict():
    r = OrderResult(status="filled", order_id="o1", symbol="BTC", side="buy", fill_price=60000)
    d = r.to_dict()
    assert d["status"] == "filled"
    assert d["success"] is True
    assert d["order_id"] == "o1"
    assert d["symbol"] == "BTC"


# ── PaperExecutor 返回值标准化 ─────────────────────────────────

def test_paper_executor_channel_name():
    pe = PaperExecutor()
    assert pe.channel_name == "paper"


def test_paper_executor_normalize_filled():
    """paper_engine 成功返回 → OrderResult(status=filled)"""
    pe = PaperExecutor()
    raw = {
        "order_id": "paper_123",
        "position_id": 456,
        "symbol": "BTC",
        "side": "buy",
        "price": 60000,
        "quantity": 0.1,
        "leverage": 5,
        "fee": 2.1,
        "status": "filled",
        "paper": True,
    }
    ctx = OrderContext(account_id=1, symbol="BTC", side="buy", quantity=0.1)
    result = pe._normalize_place_result(raw, ctx)
    assert result.status == "filled"
    assert result.success is True
    assert result.order_id == "paper_123"
    assert result.position_id == 456
    assert result.fill_price == 60000
    assert result.filled_quantity == 0.1
    assert result.fee == 2.1
    assert result.channel == "paper"


def test_paper_executor_normalize_blocked():
    """paper_engine 风控拦截返回 → OrderResult(status=blocked)"""
    pe = PaperExecutor()
    raw = {
        "success": False,
        "blocked": True,
        "blocked_layer": "deterministic",
        "blocked_by": "max_symbol_notional_pct",
        "reason": "单币种名义价值超限",
        "reason_code": "SYMBOL_NOTIONAL_EXCEEDED",
    }
    ctx = OrderContext(account_id=1, symbol="BTC", side="buy", quantity=0.1)
    result = pe._normalize_place_result(raw, ctx)
    assert result.status == "blocked"
    assert result.is_blocked is True
    assert result.success is False
    assert result.blocked_by == "max_symbol_notional_pct"
    assert result.blocked_layer == "deterministic"
    assert "单币种名义价值超限" in (result.error or "")


def test_paper_executor_normalize_rejected():
    """paper_engine 余额不足 → OrderResult(status=rejected)"""
    pe = PaperExecutor()
    raw = {
        "order_id": "paper_123",
        "symbol": "BTC",
        "side": "buy",
        "status": "rejected",
        "error": "余额不足: 需要$1200, 可用$500",
    }
    ctx = OrderContext(account_id=1, symbol="BTC", side="buy", quantity=0.1)
    result = pe._normalize_place_result(raw, ctx)
    assert result.status == "rejected"
    assert result.success is False
    assert "余额不足" in (result.error or "")


def test_paper_executor_normalize_pending():
    """paper_engine 限价单挂单 → OrderResult(status=pending)"""
    pe = PaperExecutor()
    raw = {
        "order_id": "paper_123",
        "symbol": "BTC",
        "side": "buy",
        "status": "pending",
        "price": 59000,
        "quantity": 0.1,
    }
    ctx = OrderContext(account_id=1, symbol="BTC", side="buy", quantity=0.1)
    result = pe._normalize_place_result(raw, ctx)
    assert result.status == "pending"
    assert result.success is True  # pending 也算成功


def test_paper_executor_normalize_none():
    """paper_engine 返回 None → OrderResult(status=error)"""
    pe = PaperExecutor()
    ctx = OrderContext(account_id=1, symbol="BTC", side="buy", quantity=0.1)
    result = pe._normalize_place_result(None, ctx)
    assert result.status == "error"
    assert result.success is False


def test_paper_executor_normalize_close_none():
    """close_position 返回 None（无仓位）→ OrderResult(status=rejected)"""
    pe = PaperExecutor()
    result = pe._normalize_close_result(None, "BTC", "long")
    assert result.status == "rejected"
    assert "无匹配" in (result.error or "")


def test_paper_executor_normalize_close_filled():
    """close_position 成功 → OrderResult(status=filled, pnl=...)"""
    pe = PaperExecutor()
    raw = {
        "symbol": "BTC",
        "side": "sell",
        "price": 61000,
        "quantity": 0.1,
        "pnl": 100.0,
        "fee": 2.1,
        "closed_fully": True,
    }
    result = pe._normalize_close_result(raw, "BTC", "long")
    assert result.status == "filled"
    assert result.pnl == 100.0
    assert result.fill_price == 61000


# ── LiveExecutor ────────────────────────────────────────────────

def test_live_executor_channel_name():
    le = LiveExecutor()
    assert le.channel_name == "live"


def test_live_executor_build_decision():
    """验证从 OrderContext 构造的决策 dict 结构"""
    le = LiveExecutor()
    ctx = OrderContext(
        account_id=1, symbol="BTC", side="buy", quantity=0.1,
        leverage=5, tp_price=65000, sl_price=55000,
        strategy_id="s1", trade_nature="swing", timeframe_tier="mid",
    )
    dec = le._build_decision(ctx)
    assert dec["operation"] == "buy"
    assert dec["symbol"] == "BTC"
    assert dec["side"] == "buy"
    assert dec["leverage"] == 5
    assert dec["take_profit_price"] == 65000
    assert dec["stop_loss_price"] == 55000
    assert dec["trade_nature"] == "swing"


def test_live_executor_build_decision_sell():
    le = LiveExecutor()
    ctx = OrderContext(account_id=1, symbol="BTC", side="sell", quantity=0.1)
    dec = le._build_decision(ctx)
    assert dec["operation"] == "sell"


def test_live_executor_place_order_mock():
    """LiveExecutor.place_order 委托 place_ai_driven_order（mock 验证）"""
    le = LiveExecutor()
    ctx = OrderContext(
        account_id=42, symbol="BTC", side="buy", quantity=0.1,
        leverage=5, strategy_id="s1",
    )
    with patch("backend.services.trading_commands.place_ai_driven_order") as mock_place:
        mock_place.return_value = None  # place_ai_driven_order 返回 None
        result = le.place_order(db=MagicMock(), ctx=ctx)
    assert mock_place.called
    assert mock_place.call_args.kwargs["account_id"] == 42
    trigger = mock_place.call_args.kwargs["trigger_context"]
    assert trigger["source"] == "unified_executor"
    assert trigger["strategy_id"] == "s1"
    assert len(trigger["pre_made_decisions"]) == 1
    assert result.status == "filled"  # 乐观标记
    assert result.channel == "live"
    assert result.symbol == "BTC"


def test_live_executor_place_order_exception():
    """place_ai_driven_order 抛异常 → OrderResult(status=error)"""
    le = LiveExecutor()
    ctx = OrderContext(account_id=1, symbol="BTC", side="buy", quantity=0.1)
    with patch("backend.services.trading_commands.place_ai_driven_order", side_effect=RuntimeError("网络错误")):
        result = le.place_order(db=MagicMock(), ctx=ctx)
    assert result.status == "error"
    assert "网络错误" in (result.error or "")


# ── 工厂 + 开关 ─────────────────────────────────────────────────

def test_get_executor_paper():
    executor = get_executor("paper")
    assert isinstance(executor, PaperExecutor)
    assert executor.channel_name == "paper"


def test_get_executor_live():
    executor = get_executor("live")
    assert isinstance(executor, LiveExecutor)
    assert executor.channel_name == "live"


def test_get_executor_default_paper():
    """未指定模式默认 paper"""
    executor = get_executor("")
    assert isinstance(executor, PaperExecutor)


def test_get_executor_returns_execution_channel():
    """所有执行器都实现 ExecutionChannel 接口"""
    for mode in ("paper", "live"):
        executor = get_executor(mode)
        assert isinstance(executor, ExecutionChannel)
        # 抽象方法都已实现
        assert hasattr(executor, "place_order")
        assert hasattr(executor, "close_position")
        assert hasattr(executor, "get_positions")
        assert hasattr(executor, "get_balance")


def test_is_unified_executor_enabled_default_false(monkeypatch):
    """默认关闭（灰度）"""
    monkeypatch.delenv("USE_UNIFIED_EXECUTOR", raising=False)
    assert is_unified_executor_enabled() is False


def test_is_unified_executor_enabled_true(monkeypatch):
    monkeypatch.setenv("USE_UNIFIED_EXECUTOR", "true")
    assert is_unified_executor_enabled() is True


def test_is_unified_executor_enabled_various(monkeypatch):
    for val in ("1", "yes", "on", "TRUE", "True"):
        monkeypatch.setenv("USE_UNIFIED_EXECUTOR", val)
        assert is_unified_executor_enabled() is True
    for val in ("false", "0", "no", "off", ""):
        monkeypatch.setenv("USE_UNIFIED_EXECUTOR", val)
        assert is_unified_executor_enabled() is False
