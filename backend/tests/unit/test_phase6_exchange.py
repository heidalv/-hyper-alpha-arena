"""
test_phase6_exchange — Phase 6 交易所抽象层 + 多交易所套利单元测试

覆盖范围:
1. BaseExchangeClient 接口 & 数据模型
2. HyperliquidAdapter
3. BinanceAdapter
4. ExchangeClientFactory
5. CrossExchangeArbitrageEngine
6. CrossExchangeRiskTracker & LegRiskManager
7. 集成测试
"""

import asyncio
from unittest.mock import MagicMock, AsyncMock

import pytest

from backend.services.exchange.base_exchange_client import (
    BaseExchangeClient,
    ExchangeOrder,
    ExchangePosition,
    ExchangeBalance,
    OrderSide,
    OrderType,
    ExchangeType,
)
from backend.services.exchange.hyperliquid_adapter import HyperliquidAdapter
from backend.services.exchange.binance_adapter import BinanceAdapter
from backend.services.exchange.exchange_factory import ExchangeClientFactory
from backend.services.exchange.cross_exchange_arb import (
    CrossExchangeArbitrageEngine,
    CrossExchangeSpread,
    CrossExchangeTrade,
)
from backend.services.exchange.cross_exchange_risk import (
    CrossExchangeRiskTracker,
    CrossExchangeExposure,
    CrossExchangeRiskCheckResult,
    LegRiskManager,
    DEFAULT_RISK_RULES,
)


# ════════════════════════════════════════════════════════
#  1. Data Model Tests
# ════════════════════════════════════════════════════════

class TestExchangeOrder:
    def test_creation_defaults(self):
        order = ExchangeOrder(
            order_id="o1", symbol="BTC", side=OrderSide.BUY,
            order_type=OrderType.MARKET, size=1.0,
        )
        assert order.price is None
        assert order.sl is None
        assert order.tp is None
        assert order.leverage == 1
        assert order.reduce_only is False

    def test_notional_value(self):
        order = ExchangeOrder(
            order_id="o1", symbol="BTC", side=OrderSide.BUY,
            order_type=OrderType.LIMIT, size=2.0, price=50000.0,
        )
        assert order.notional_value == 100000.0

    def test_notional_value_no_price(self):
        order = ExchangeOrder(
            order_id="o1", symbol="BTC", side=OrderSide.BUY,
            order_type=OrderType.MARKET, size=2.0,
        )
        assert order.notional_value == 0.0


class TestExchangePosition:
    def test_notional_value(self):
        pos = ExchangePosition(
            symbol="BTC", side="long", size=1.0,
            entry_price=50000, mark_price=51000,
            unrealized_pnl=1000, margin=5000, leverage=10,
        )
        assert pos.notional_value == 51000.0


class TestExchangeBalance:
    def test_margin_ratio(self):
        bal = ExchangeBalance(
            total_equity=10000, available_balance=5000,
            frozen_margin=3000, unrealized_pnl=2000,
        )
        assert abs(bal.margin_ratio - 0.3) < 1e-10

    def test_margin_ratio_zero_equity(self):
        bal = ExchangeBalance(0, 0, 0, 0)
        assert bal.margin_ratio == 0.0


class TestEnums:
    def test_order_side(self):
        assert OrderSide.BUY.value == "buy"
        assert OrderSide.SELL.value == "sell"

    def test_order_type(self):
        assert OrderType.MARKET.value == "market"
        assert OrderType.LIMIT.value == "limit"

    def test_exchange_type(self):
        assert len(ExchangeType) == 6
        assert ExchangeType.HYPERLIQUID.value == "hyperliquid"
        assert ExchangeType.BINANCE.value == "binance"
        assert ExchangeType.ASTERDEX.value == "asterdex"


# ════════════════════════════════════════════════════════
#  2. HyperliquidAdapter Tests
# ════════════════════════════════════════════════════════

class TestHyperliquidAdapter:
    def test_exchange_type(self):
        adapter = HyperliquidAdapter()
        assert adapter.exchange_type == ExchangeType.HYPERLIQUID

    def test_supports(self):
        adapter = HyperliquidAdapter()
        assert adapter.supports_spot is False
        assert adapter.supports_futures is True

    def test_get_balance_no_client(self):
        adapter = HyperliquidAdapter()
        bal = asyncio.get_event_loop().run_until_complete(adapter.get_balance())
        assert bal.total_equity == 0
        assert isinstance(bal, ExchangeBalance)

    def test_get_positions_no_client(self):
        adapter = HyperliquidAdapter()
        positions = asyncio.get_event_loop().run_until_complete(adapter.get_positions())
        assert positions == []

    def test_place_order_no_client(self):
        adapter = HyperliquidAdapter()
        order = ExchangeOrder(
            order_id="o1", symbol="BTC", side=OrderSide.BUY,
            order_type=OrderType.MARKET, size=1.0,
        )
        result = asyncio.get_event_loop().run_until_complete(adapter.place_order(order))
        assert result['status'] == 'error'

    def test_cancel_order_no_client(self):
        adapter = HyperliquidAdapter()
        result = asyncio.get_event_loop().run_until_complete(adapter.cancel_order("o1", "BTC"))
        assert result is False

    def test_get_funding_rate_no_client(self):
        adapter = HyperliquidAdapter()
        rate = asyncio.get_event_loop().run_until_complete(adapter.get_funding_rate("BTC"))
        assert rate == 0.0

    def test_get_all_funding_rates_no_client(self):
        adapter = HyperliquidAdapter()
        # ccxt 已安装时会连 Hyperliquid 公开 API 返回真实数据，仅验证类型
        rates = asyncio.get_event_loop().run_until_complete(adapter.get_all_funding_rates())
        assert isinstance(rates, dict)

    def test_get_orderbook_no_client(self):
        adapter = HyperliquidAdapter()
        book = asyncio.get_event_loop().run_until_complete(adapter.get_orderbook("BTC"))
        assert book == {'bids': [], 'asks': []}

    def test_get_klines_no_client(self):
        adapter = HyperliquidAdapter()
        klines = asyncio.get_event_loop().run_until_complete(adapter.get_klines("BTC", "1h"))
        assert klines == []

    def test_get_balance_with_mock_client(self):
        mock_client = MagicMock()
        mock_client.get_account_state = MagicMock(return_value={
            'total_equity': 10000, 'available_balance': 8000,
            'used_margin': 1500,
        })
        adapter = HyperliquidAdapter(mock_client)
        bal = asyncio.get_event_loop().run_until_complete(adapter.get_balance())
        assert bal.total_equity == 10000
        assert bal.available_balance == 8000

    def test_get_balance_with_error(self):
        mock_client = MagicMock()
        mock_client.get_account_state = MagicMock(side_effect=Exception("conn error"))
        adapter = HyperliquidAdapter(mock_client)
        bal = asyncio.get_event_loop().run_until_complete(adapter.get_balance())
        assert bal.total_equity == 0

    def test_get_positions_with_mock_client(self):
        mock_client = MagicMock()
        mock_client.get_positions = MagicMock(return_value=[
            {'coin': 'BTC', 'szi': 0.5,
             'entryPx': 50000, 'markPx': 51000,
             'unrealizedPnl': 500, 'marginUsed': 2500, 'leverage': 10},
        ])
        adapter = HyperliquidAdapter(mock_client)
        positions = asyncio.get_event_loop().run_until_complete(adapter.get_positions())
        assert len(positions) == 1
        assert positions[0].symbol == 'BTC'
        assert positions[0].size == 0.5

    def test_place_order_with_mock_client(self):
        mock_client = MagicMock()
        mock_client.place_order = MagicMock(return_value={'status': 'ok', 'order_id': '123'})
        adapter = HyperliquidAdapter(mock_client)
        order = ExchangeOrder(
            order_id="o1", symbol="BTC", side=OrderSide.BUY,
            order_type=OrderType.MARKET, size=1.0,
        )
        result = asyncio.get_event_loop().run_until_complete(adapter.place_order(order))
        assert result['status'] == 'ok'


# ════════════════════════════════════════════════════════
#  3. BinanceAdapter Tests
# ════════════════════════════════════════════════════════

class TestBinanceAdapter:
    def test_exchange_type(self):
        adapter = BinanceAdapter()
        assert adapter.exchange_type == ExchangeType.BINANCE

    def test_supports(self):
        adapter = BinanceAdapter()
        assert adapter.supports_spot is True
        assert adapter.supports_futures is True

    def test_get_balance_no_ccxt(self):
        adapter = BinanceAdapter()
        bal = asyncio.get_event_loop().run_until_complete(adapter.get_balance())
        assert bal.total_equity == 0

    def test_get_positions_no_ccxt(self):
        adapter = BinanceAdapter()
        positions = asyncio.get_event_loop().run_until_complete(adapter.get_positions())
        assert positions == []

    def test_place_order_no_ccxt(self):
        adapter = BinanceAdapter()
        order = ExchangeOrder(
            order_id="o1", symbol="BTC", side=OrderSide.BUY,
            order_type=OrderType.MARKET, size=1.0,
        )
        result = asyncio.get_event_loop().run_until_complete(adapter.place_order(order))
        assert result['status'] == 'error'

    def test_get_funding_rate_no_ccxt(self):
        adapter = BinanceAdapter()
        rate = asyncio.get_event_loop().run_until_complete(adapter.get_funding_rate("BTC"))
        assert rate == 0.0

    def test_get_orderbook_no_ccxt(self):
        adapter = BinanceAdapter()
        book = asyncio.get_event_loop().run_until_complete(adapter.get_orderbook("BTC"))
        assert book == {'bids': [], 'asks': []}

    def test_get_klines_no_ccxt(self):
        adapter = BinanceAdapter()
        klines = asyncio.get_event_loop().run_until_complete(adapter.get_klines("BTC", "1h"))
        assert klines == []


# ════════════════════════════════════════════════════════
#  4. ExchangeClientFactory Tests
# ════════════════════════════════════════════════════════

class TestExchangeClientFactory:
    def test_create_hyperliquid(self):
        client = ExchangeClientFactory.create('hyperliquid')
        assert isinstance(client, HyperliquidAdapter)
        assert client.exchange_type == ExchangeType.HYPERLIQUID

    def test_create_binance(self):
        client = ExchangeClientFactory.create('binance')
        assert isinstance(client, BinanceAdapter)
        assert client.exchange_type == ExchangeType.BINANCE

    def test_create_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown exchange"):
            ExchangeClientFactory.create('unknown_exchange')

    def test_get_registered_exchanges(self):
        registered = ExchangeClientFactory.get_registered_exchanges()
        assert 'hyperliquid' in registered
        assert 'binance' in registered

    def test_is_registered(self):
        assert ExchangeClientFactory.is_registered('hyperliquid') is True
        assert ExchangeClientFactory.is_registered('unknown') is False

    def test_register_custom(self):
        class StubAdapter(BaseExchangeClient):
            @property
            def exchange_type(self): return ExchangeType.ASTERDEX
            @property
            def supports_spot(self): return True
            @property
            def supports_futures(self): return True
            async def get_balance(self): return ExchangeBalance(0, 0, 0, 0)
            async def get_positions(self): return []
            async def place_order(self, order): return {}
            async def cancel_order(self, order_id, symbol): return True
            async def get_funding_rate(self, symbol): return 0.0
            async def get_all_funding_rates(self): return {}
            async def get_orderbook(self, symbol, depth=20): return {'bids': [], 'asks': []}
            async def get_klines(self, symbol, interval, limit=100): return []

        ExchangeClientFactory.register('aster', StubAdapter)
        assert ExchangeClientFactory.is_registered('aster')
        client = ExchangeClientFactory.create('aster')
        assert isinstance(client, StubAdapter)
        # cleanup
        del ExchangeClientFactory._registry['aster']

    def test_create_with_kwargs(self):
        client = ExchangeClientFactory.create('hyperliquid', existing_client=None)
        assert isinstance(client, HyperliquidAdapter)


# ════════════════════════════════════════════════════════
#  5. CrossExchangeSpread Tests
# ════════════════════════════════════════════════════════

class TestCrossExchangeSpread:
    def test_creation(self):
        spread = CrossExchangeSpread(
            symbol="BTC", exchange_a="hyperliquid", exchange_b="binance",
            price_a=50000, price_b=49900, spread_pct=0.2,
            historical_mean=0.1, historical_std=0.05, z_score=2.0,
        )
        assert spread.symbol == "BTC"
        assert spread.spread_pct == 0.2
        assert spread.z_score == 2.0

    def test_direction_a_above(self):
        spread = CrossExchangeSpread(
            symbol="BTC", exchange_a="a", exchange_b="b",
            price_a=50000, price_b=49900, spread_pct=0.2,
            historical_mean=0.1, historical_std=0.05, z_score=2.0,
        )
        assert spread.direction == "a_above_b"

    def test_direction_a_below(self):
        spread = CrossExchangeSpread(
            symbol="BTC", exchange_a="a", exchange_b="b",
            price_a=49900, price_b=50000, spread_pct=-0.2,
            historical_mean=0.1, historical_std=0.05, z_score=-2.0,
        )
        assert spread.direction == "a_below_b"


# ════════════════════════════════════════════════════════
#  6. CrossExchangeArbitrageEngine Tests
# ════════════════════════════════════════════════════════

def _make_mock_client(exchange_name="hyperliquid"):
    """创建返回预设 orderbook 的 mock client"""
    mock = MagicMock()
    mock.exchange_type = MagicMock()
    mock.exchange_type.value = exchange_name
    mock.get_orderbook = AsyncMock(return_value={
        'bids': [[100.0, 10], [99.0, 20]],
        'asks': [[101.0, 10], [102.0, 20]],
    })
    return mock


class TestCrossExchangeArbitrageEngine:
    def test_scan_spreads_basic(self):
        client_a = _make_mock_client("exchange_a")
        client_b = _make_mock_client("exchange_b")
        engine = CrossExchangeArbitrageEngine(client_a, client_b)

        spreads = asyncio.get_event_loop().run_until_complete(
            engine.scan_spreads(["BTC", "ETH"])
        )
        assert len(spreads) == 2
        assert spreads[0].symbol == "BTC"
        # mid_a = (100+101)/2 = 100.5, mid_b = (100+101)/2 = 100.5
        assert abs(spreads[0].spread_pct) < 0.01

    def test_scan_spreads_empty_symbols(self):
        client_a = _make_mock_client()
        client_b = _make_mock_client()
        engine = CrossExchangeArbitrageEngine(client_a, client_b)
        spreads = asyncio.get_event_loop().run_until_complete(
            engine.scan_spreads([])
        )
        assert spreads == []

    def test_scan_spreads_handles_error(self):
        client_a = _make_mock_client()
        client_b = MagicMock()
        client_b.exchange_type = MagicMock()
        client_b.exchange_type.value = "b"
        client_b.get_orderbook = AsyncMock(side_effect=Exception("conn error"))
        engine = CrossExchangeArbitrageEngine(client_a, client_b)
        spreads = asyncio.get_event_loop().run_until_complete(
            engine.scan_spreads(["BTC"])
        )
        assert spreads == []

    def test_find_entry_opportunities_insufficient_history(self):
        client_a = _make_mock_client()
        client_b = _make_mock_client()
        engine = CrossExchangeArbitrageEngine(client_a, client_b)
        # Only 1 data point, less than MIN_HISTORY_FOR_STATS=5
        spread = CrossExchangeSpread(
            symbol="BTC", exchange_a="a", exchange_b="b",
            price_a=50000, price_b=49900, spread_pct=0.2,
            historical_mean=0.0, historical_std=0.01, z_score=5.0,
        )
        opportunities = engine.find_entry_opportunities([spread])
        assert len(opportunities) == 0

    def test_find_entry_opportunities_with_history(self):
        client_a = _make_mock_client("a")
        client_b = _make_mock_client("b")
        engine = CrossExchangeArbitrageEngine(client_a, client_b)

        # Build up history
        for _ in range(10):
            asyncio.get_event_loop().run_until_complete(
                engine.scan_spreads(["BTC"])
            )

        # Now create a spread with high z_score
        spread = CrossExchangeSpread(
            symbol="BTC", exchange_a="a", exchange_b="b",
            price_a=50000, price_b=49900, spread_pct=5.0,
            historical_mean=0.0, historical_std=0.01, z_score=5.0,
        )
        # Need to inject history key manually
        key = "BTC_a_b"
        engine._spread_history[key] = [0.0] * 10

        opportunities = engine.find_entry_opportunities([spread])
        assert len(opportunities) == 1

    def test_generate_trade_orders_positive_z(self):
        client_a = _make_mock_client("a")
        client_b = _make_mock_client("b")
        engine = CrossExchangeArbitrageEngine(client_a, client_b)

        spread = CrossExchangeSpread(
            symbol="BTC", exchange_a="a", exchange_b="b",
            price_a=50000, price_b=49900, spread_pct=0.2,
            historical_mean=0.0, historical_std=0.01, z_score=3.0,
        )
        order_a, order_b = engine.generate_trade_orders(spread, equity=10000, size=0.1)
        assert order_a.side == OrderSide.SELL  # A is expensive, sell on A
        assert order_b.side == OrderSide.BUY   # B is cheap, buy on B
        assert order_a.size == 0.1

    def test_generate_trade_orders_negative_z(self):
        client_a = _make_mock_client("a")
        client_b = _make_mock_client("b")
        engine = CrossExchangeArbitrageEngine(client_a, client_b)

        spread = CrossExchangeSpread(
            symbol="BTC", exchange_a="a", exchange_b="b",
            price_a=49900, price_b=50000, spread_pct=-0.2,
            historical_mean=0.0, historical_std=0.01, z_score=-3.0,
        )
        order_a, order_b = engine.generate_trade_orders(spread, equity=10000, size=0.1)
        assert order_a.side == OrderSide.BUY   # A is cheap, buy on A
        assert order_b.side == OrderSide.SELL   # B is expensive, sell on B

    def test_close_trade(self):
        client_a = _make_mock_client("a")
        client_b = _make_mock_client("b")
        engine = CrossExchangeArbitrageEngine(client_a, client_b)

        spread = CrossExchangeSpread(
            symbol="BTC", exchange_a="a", exchange_b="b",
            price_a=50000, price_b=49900, spread_pct=0.2,
            historical_mean=0.0, historical_std=0.01, z_score=3.0,
        )
        engine.generate_trade_orders(spread, equity=10000, size=0.1)
        active = engine.get_active_trades()
        assert len(active) == 1
        trade_id = list(active.keys())[0]

        closed = engine.close_trade(trade_id, exit_spread_pct=0.05)
        assert closed is not None
        assert closed.status == "closed"
        assert closed.exit_spread_pct == 0.05
        assert closed.pnl is not None

        assert len(engine.get_active_trades()) == 0

    def test_close_trade_unknown_id(self):
        client_a = _make_mock_client()
        client_b = _make_mock_client()
        engine = CrossExchangeArbitrageEngine(client_a, client_b)
        result = engine.close_trade("nonexistent", 0.0)
        assert result is None

    def test_get_spread_history(self):
        client_a = _make_mock_client("a")
        client_b = _make_mock_client("b")
        engine = CrossExchangeArbitrageEngine(client_a, client_b)
        asyncio.get_event_loop().run_until_complete(
            engine.scan_spreads(["BTC"])
        )
        history = engine.get_spread_history("BTC")
        assert len(history) == 1

    def test_trade_count(self):
        client_a = _make_mock_client()
        client_b = _make_mock_client()
        engine = CrossExchangeArbitrageEngine(client_a, client_b)
        assert engine.trade_count == 0
        spread = CrossExchangeSpread(
            symbol="BTC", exchange_a="a", exchange_b="b",
            price_a=50000, price_b=49900, spread_pct=0.2,
            historical_mean=0.0, historical_std=0.01, z_score=3.0,
        )
        engine.generate_trade_orders(spread, equity=10000, size=0.1)
        assert engine.trade_count == 1


# ════════════════════════════════════════════════════════
#  7. CrossExchangeRiskTracker Tests
# ════════════════════════════════════════════════════════

def _make_position(symbol, size, price, margin):
    return ExchangePosition(
        symbol=symbol, side="long", size=size,
        entry_price=price, mark_price=price,
        unrealized_pnl=0, margin=margin, leverage=10,
    )


class TestCrossExchangeExposure:
    def test_creation(self):
        exp = CrossExchangeExposure(
            exchange="test", total_margin=1000,
            total_notional=10000, position_count=2,
        )
        assert exp.symbols == []


class TestCrossExchangeRiskCheckResult:
    def test_is_safe(self):
        r = CrossExchangeRiskCheckResult(passed=True)
        assert r.is_safe is True

    def test_not_safe(self):
        r = CrossExchangeRiskCheckResult(passed=False, violations=["exceeded"])
        assert r.is_safe is False


class TestCrossExchangeRiskTracker:
    def test_calculate_correlation_no_overlap(self):
        tracker = CrossExchangeRiskTracker()
        pa = [_make_position("BTC", 1, 50000, 5000)]
        pb = [_make_position("ETH", 10, 3000, 3000)]
        assert tracker.calculate_correlation(pa, pb) == 0.0

    def test_calculate_correlation_full_overlap(self):
        tracker = CrossExchangeRiskTracker()
        pa = [_make_position("BTC", 1, 50000, 5000)]
        pb = [_make_position("BTC", 1, 50000, 5000)]
        assert tracker.calculate_correlation(pa, pb) == 1.0

    def test_calculate_correlation_partial(self):
        tracker = CrossExchangeRiskTracker()
        pa = [_make_position("BTC", 1, 50000, 5000), _make_position("ETH", 10, 3000, 3000)]
        pb = [_make_position("BTC", 1, 50000, 5000)]
        assert tracker.calculate_correlation(pa, pb) == 0.5

    def test_calculate_correlation_empty(self):
        tracker = CrossExchangeRiskTracker()
        assert tracker.calculate_correlation([], []) == 0.0

    def test_calculate_exposure(self):
        tracker = CrossExchangeRiskTracker()
        positions = [
            _make_position("BTC", 1, 50000, 5000),
            _make_position("ETH", 10, 3000, 3000),
        ]
        exp = tracker.calculate_exposure(positions, "test_exchange")
        assert exp.exchange == "test_exchange"
        assert exp.total_margin == 8000
        assert exp.total_notional == 80000
        assert exp.position_count == 2
        assert set(exp.symbols) == {"BTC", "ETH"}

    def test_check_risk_passes(self):
        tracker = CrossExchangeRiskTracker()
        pa = [_make_position("BTC", 0.01, 50000, 50)]
        pb = [_make_position("BTC", 0.01, 50000, 50)]
        result = tracker.check_risk(pa, pb, equity=100000)
        assert result.passed is True
        assert len(result.violations) == 0

    def test_check_risk_zero_equity(self):
        tracker = CrossExchangeRiskTracker()
        pa = [_make_position("BTC", 1, 50000, 5000)]
        result = tracker.check_risk(pa, [], equity=0)
        assert result.passed is False

    def test_check_risk_exposure_exceeded(self):
        tracker = CrossExchangeRiskTracker()
        # 50000 notional on equity 100000 = 50% > 20% limit
        pa = [_make_position("BTC", 1, 50000, 5000)]
        pb = [_make_position("ETH", 10, 3000, 3000)]
        result = tracker.check_risk(pa, pb, equity=100000)
        assert result.passed is False
        assert any("exposure" in v for v in result.violations)

    def test_check_risk_hedge_delta_exceeded(self):
        tracker = CrossExchangeRiskTracker()
        # A has 50000 notional BTC, B has 10000 notional BTC → delta=40000/100000=40% > 2%
        pa = [_make_position("BTC", 1, 50000, 5000)]
        pb = [_make_position("BTC", 0.2, 50000, 1000)]
        result = tracker.check_risk(pa, pb, equity=100000)
        assert result.passed is False
        assert any("hedge delta" in v for v in result.violations)

    def test_default_rules(self):
        tracker = CrossExchangeRiskTracker()
        rules = tracker.rules
        assert rules['max_hedge_delta_pct'] == 0.02
        assert rules['max_total_arbitrage_pct'] == 0.40
        assert rules['max_cross_exchange_exposure'] == 0.20

    def test_custom_rules(self):
        custom = {'max_hedge_delta_pct': 0.05, 'max_total_arbitrage_pct': 0.60, 'max_cross_exchange_exposure': 0.30}
        tracker = CrossExchangeRiskTracker(rules=custom)
        assert tracker.rules['max_hedge_delta_pct'] == 0.05


# ════════════════════════════════════════════════════════
#  8. LegRiskManager Tests
# ════════════════════════════════════════════════════════

class TestLegRiskManager:
    def test_retry_succeeds(self):
        mock_client = MagicMock()
        mock_client.place_order = AsyncMock(return_value={'status': 'ok'})
        mgr = LegRiskManager()

        executed = ExchangeOrder(
            order_id="e1", symbol="BTC", side=OrderSide.BUY,
            order_type=OrderType.MARKET, size=0.1,
        )
        failed = ExchangeOrder(
            order_id="f1", symbol="BTC", side=OrderSide.SELL,
            order_type=OrderType.MARKET, size=0.1,
        )
        result = asyncio.get_event_loop().run_until_complete(
            mgr.handle_single_leg(executed, failed, mock_client)
        )
        assert result is True

    def test_retry_fails_then_emergency_close(self):
        call_count = [0]
        async def mock_place(order):
            call_count[0] += 1
            if call_count[0] <= 3:
                raise Exception("retry failed")
            return {'status': 'ok'}

        mock_client = MagicMock()
        mock_client.place_order = mock_place
        mgr = LegRiskManager()

        executed = ExchangeOrder(
            order_id="e1", symbol="BTC", side=OrderSide.BUY,
            order_type=OrderType.MARKET, size=0.1,
        )
        failed = ExchangeOrder(
            order_id="f1", symbol="BTC", side=OrderSide.SELL,
            order_type=OrderType.MARKET, size=0.1,
        )
        result = asyncio.get_event_loop().run_until_complete(
            mgr.handle_single_leg(executed, failed, mock_client)
        )
        # Emergency close should succeed (4th call), but returns False
        assert result is False
        assert call_count[0] == 4  # 3 retries + 1 emergency close


# ════════════════════════════════════════════════════════
#  9. Integration Tests
# ════════════════════════════════════════════════════════

class TestPhase6Integration:
    def test_all_phase6_modules_importable(self):
        from backend.services.exchange import (
            BaseExchangeClient, ExchangeOrder, ExchangePosition, ExchangeBalance,
            OrderSide, OrderType, ExchangeType,
            HyperliquidAdapter, BinanceAdapter, ExchangeClientFactory,
        )
        from backend.services.exchange.cross_exchange_arb import (
            CrossExchangeArbitrageEngine, CrossExchangeSpread,
        )
        from backend.services.exchange.cross_exchange_risk import (
            CrossExchangeRiskTracker, LegRiskManager,
        )
        assert BaseExchangeClient is not None
        assert CrossExchangeArbitrageEngine is not None
        assert CrossExchangeRiskTracker is not None
        assert LegRiskManager is not None

    def test_factory_to_engine_pipeline(self):
        """工厂创建适配器 → 构建套利引擎"""
        client_a = ExchangeClientFactory.create('hyperliquid')
        client_b = ExchangeClientFactory.create('binance')
        engine = CrossExchangeArbitrageEngine(client_a, client_b)
        assert engine.client_a.exchange_type == ExchangeType.HYPERLIQUID
        assert engine.client_b.exchange_type == ExchangeType.BINANCE

    def test_engine_to_risk_pipeline(self):
        """引擎交易 → 风控检查"""
        client_a = _make_mock_client("a")
        client_b = _make_mock_client("b")
        engine = CrossExchangeArbitrageEngine(client_a, client_b)

        spread = CrossExchangeSpread(
            symbol="BTC", exchange_a="a", exchange_b="b",
            price_a=50000, price_b=49900, spread_pct=0.2,
            historical_mean=0.0, historical_std=0.01, z_score=3.0,
        )
        engine.generate_trade_orders(spread, equity=10000, size=0.01)

        # Simulate positions from both exchanges
        pa = [_make_position("BTC", 0.01, 50000, 50)]
        pb = [_make_position("BTC", 0.01, 50000, 50)]
        tracker = CrossExchangeRiskTracker()
        result = tracker.check_risk(pa, pb, equity=100000)
        assert result.passed is True

    def test_full_scan_trade_close_cycle(self):
        """完整扫描 → 开仓 → 平仓流程"""
        # Setup mock clients with slight price difference
        client_a = MagicMock()
        client_a.exchange_type = MagicMock()
        client_a.exchange_type.value = "exchange_a"
        client_a.get_orderbook = AsyncMock(return_value={
            'bids': [[100.0, 10]], 'asks': [[101.0, 10]],
        })

        client_b = MagicMock()
        client_b.exchange_type = MagicMock()
        client_b.exchange_type.value = "exchange_b"
        client_b.get_orderbook = AsyncMock(return_value={
            'bids': [[99.0, 10]], 'asks': [[100.0, 10]],
        })

        engine = CrossExchangeArbitrageEngine(client_a, client_b)

        # Scan multiple times to build history
        for _ in range(10):
            asyncio.get_event_loop().run_until_complete(
                engine.scan_spreads(["BTC"])
            )

        # Generate trade
        spread = CrossExchangeSpread(
            symbol="BTC", exchange_a="exchange_a", exchange_b="exchange_b",
            price_a=100.5, price_b=99.5, spread_pct=1.0,
            historical_mean=0.0, historical_std=0.01, z_score=3.0,
        )
        order_a, order_b = engine.generate_trade_orders(spread, equity=10000, size=1.0)
        assert order_a.side == OrderSide.SELL
        assert order_b.side == OrderSide.BUY

        # Close trade
        active = engine.get_active_trades()
        trade_id = list(active.keys())[0]
        closed = engine.close_trade(trade_id, exit_spread_pct=0.1)
        assert closed.status == "closed"
        assert closed.pnl is not None
