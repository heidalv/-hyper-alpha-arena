"""
Phase 3 套利组件测试 — mid cache / alerts / execution authority
"""

import pytest
import time


@pytest.mark.integration
class TestMidCache:
    def test_set_and_get_mid(self):
        from backend.services.arbitrage.cross_exchange_mid_cache import CrossExchangeMidCache

        cache = CrossExchangeMidCache(ttl_sec=10.0)
        cache.set_mid("binance", "BTC", 50000.0, 49999.0, 50001.0)
        entry = cache.get_mid("binance", "BTC")
        assert entry is not None
        assert entry.mid_price == 50000.0

    def test_stale_entry_returns_none(self):
        from backend.services.arbitrage.cross_exchange_mid_cache import (
            CrossExchangeMidCache,
            MidPriceEntry,
        )

        cache = CrossExchangeMidCache(ttl_sec=0.001)
        cache._data[("binance", "ETH")] = MidPriceEntry(
            exchange="binance", symbol="ETH", mid_price=3000,
            bid=2999, ask=3001, updated_at=time.time() - 10,
        )
        assert cache.get_mid("binance", "ETH") is None

    def test_refresh_from_orderbook(self):
        from backend.services.arbitrage.cross_exchange_mid_cache import CrossExchangeMidCache

        cache = CrossExchangeMidCache()
        mid = cache.refresh_from_orderbook("hyperliquid", "SOL", {
            "bids": [[100.0, 1]],
            "asks": [[102.0, 1]],
        })
        assert mid == 101.0


@pytest.mark.integration
class TestArbAlertMonitor:
    def test_emit_and_retrieve(self):
        from backend.services.arbitrage.arbitrage_alert_monitor import ArbitrageAlertMonitor

        mon = ArbitrageAlertMonitor()
        mon.emit("warning", "test_alert", "test message", force=True)
        alerts = mon.get_alerts(limit=5)
        assert any(a["code"] == "test_alert" for a in alerts)

    def test_pool_utilization_critical(self):
        from backend.services.arbitrage.arbitrage_alert_monitor import ArbitrageAlertMonitor

        mon = ArbitrageAlertMonitor()
        mon._last_emit.clear()
        mon.check_pool_utilization({
            "allocations": {"rebate_points_arb": 100},
            "used": {"rebate_points_arb": 96},
        })
        alerts = mon.get_alerts(code="pool_exhaustion", limit=5)
        assert len(alerts) >= 1


@pytest.mark.integration
class TestExecutionAuthority:
    def test_status_shows_fullauto_authority(self):
        from backend.services.arbitrage.execution_authority import execution_authority

        status = execution_authority.get_status()
        assert status["authority"] == "fullauto"
        assert status["qaa_plugins_mode"] == "read_only"
        assert "market_data_hub" in status

    def test_scan_rebate_via_authority(self):
        from backend.services.arbitrage.execution_authority import (
            ExecutionAuthority,
            ExecutionSource,
        )

        result = ExecutionAuthority.scan_rebate_strategies(
            account_equity=300.0,
            source=ExecutionSource.API,
        )
        assert result["execution_source"] == "api"
        assert result["triggered"] is True
        assert result["total_evaluated"] >= 1


@pytest.mark.integration
class TestMarketDataHub:
    def test_publish_l2_updates_mid_cache(self):
        from backend.services.market_data_hub import market_data_hub
        from backend.services.arbitrage.cross_exchange_mid_cache import mid_cache

        market_data_hub._wire_adapters()
        market_data_hub.publish_l2_book("binance", "ETH", {
            "bids": [[3000.0, 1.0]],
            "asks": [[3002.0, 1.0]],
        })
        entry = mid_cache.get_mid("binance", "ETH")
        assert entry is not None
        assert entry.mid_price == 3001.0

    def test_hub_get_l2(self):
        from backend.services.market_data_hub import market_data_hub

        market_data_hub.publish_l2_book("bybit", "SOL", {
            "bids": [[180.0, 2.0]],
            "asks": [[182.0, 2.0]],
        })
        snap = market_data_hub.get_l2("bybit", "SOL")
        assert snap is not None
        assert snap.mid == 181.0

    def test_hub_status(self):
        from backend.services.market_data_hub import market_data_hub

        status = market_data_hub.get_status()
        assert "publish_count" in status
        assert "l2_entries" in status

    def test_get_market_snapshot(self):
        from backend.services.market_data_hub import market_data_hub

        market_data_hub.publish_l2_book("hyperliquid", "BTC", {
            "bids": [[95000.0, 1.0]],
            "asks": [[95002.0, 1.0]],
        })
        market_data_hub.publish_funding("hyperliquid", "BTC", {"rate": 0.0001})
        snap = market_data_hub.get_market_snapshot("BTC")
        assert snap["price"] == 95001.0
        assert snap["funding_rate"] == 0.0001

    def test_should_disable_rest_when_hub_running(self):
        from backend.services.market_data_hub import market_data_hub

        market_data_hub._running = True
        market_data_hub._disable_rest_market_stream = True
        assert market_data_hub.should_disable_rest_market_stream() is True
        market_data_hub._disable_rest_market_stream = False
        assert market_data_hub.should_disable_rest_market_stream() is False


@pytest.mark.integration
class TestMarketPriceService:
    def test_get_price_from_hub(self):
        from backend.services.market_data_hub import market_data_hub
        from backend.services.market_price_service import get_price

        market_data_hub.publish_l2_book("hyperliquid", "BTC", {
            "bids": [[100.0, 1.0]],
            "asks": [[102.0, 1.0]],
        })
        assert get_price("BTC") == 101.0

    def test_get_market_snapshots(self):
        from backend.services.market_data_hub import market_data_hub
        from backend.services.market_price_service import get_market_snapshots

        market_data_hub.publish_l2_book("hyperliquid", "SOL", {
            "bids": [[180.0, 1.0]],
            "asks": [[182.0, 1.0]],
        })
        market_data_hub.publish_asset_ctx("hyperliquid", "SOL", {
            "ctx": {
                "openInterest": "1000000",
                "funding": "0.0001",
                "markPx": "181.5",
                "dayNtlVlm": "50000000",
                "prevDayPx": "175.0",
            }
        })
        snaps = get_market_snapshots(["SOL"])
        assert snaps["SOL"]["price"] == 181.0
        assert snaps["SOL"]["open_interest"] == 1000000.0
        assert snaps["SOL"]["funding_rate"] == 0.0001

    def test_sync_market_symbols_skips_rest_when_hub_active(self, monkeypatch):
        from backend.services import market_price_service as mps

        class _Hub:
            _running = True
            _disable_rest_market_stream = True
            updated = []

            def should_disable_rest_market_stream(self):
                return True

            def update_symbols(self, symbols):
                self.updated = symbols

        monkeypatch.setattr(
            "backend.services.market_data_hub.market_data_hub",
            _Hub(),
        )
        mps._legacy_poller = None
        mps.sync_market_symbols(["BTC", "ETH"])
        assert mps.is_legacy_rest_poller_running() is False


@pytest.mark.integration
class TestUnifiedDataPoolHub:
    def test_capture_market_data_uses_hub_only(self):
        from backend.services.market_data_hub import market_data_hub
        from backend.services.unified_data_pool import UnifiedDataPool

        market_data_hub._running = True
        market_data_hub.publish_l2_book("hyperliquid", "ETH", {
            "bids": [[3500.0, 1.0]],
            "asks": [[3502.0, 1.0]],
        })
        market_data_hub.publish_asset_ctx("hyperliquid", "ETH", {
            "ctx": {"funding": "0.0002", "openInterest": "999", "markPx": "3501"}
        })
        pool = UnifiedDataPool()
        markets = pool._capture_market_data(["ETH"], "mainnet")
        assert "ETH" in markets
        assert markets["ETH"].price == 3501.0
        assert markets["ETH"].funding_rate == 0.0002
        assert markets["ETH"].open_interest == 999.0


@pytest.mark.integration
class TestMarketPricesApi:
    def test_hub_snapshots_route(self):
        from backend.services.market_data_hub import market_data_hub
        from backend.api.market_data_routes import get_hub_market_snapshots

        market_data_hub.publish_l2_book("hyperliquid", "BTC", {
            "bids": [[90000.0, 1.0]],
            "asks": [[90002.0, 1.0]],
        })
        resp = get_hub_market_snapshots(symbols="BTC")
        assert resp["count"] == 1
        assert resp["snapshots"]["BTC"]["price"] == 90001.0


@pytest.mark.integration
class TestWsFeed:
    def test_push_hyperliquid_l2book(self):
        from backend.services.arbitrage.cross_exchange_ws_feed import push_hyperliquid_l2book
        from backend.services.arbitrage.cross_exchange_mid_cache import mid_cache

        push_hyperliquid_l2book("BTC", {
            "levels": [
                [{"px": "50000", "sz": "1"}],
                [{"px": "50002", "sz": "1"}],
            ],
        })
        entry = mid_cache.get_mid("hyperliquid", "BTC")
        assert entry is not None
        assert entry.mid_price == 50001.0

    def test_ws_feed_status(self):
        from backend.services.arbitrage.cross_exchange_ws_feed import cross_exchange_ws_feed

        status = cross_exchange_ws_feed.get_status()
        assert "feed_running" in status
        assert "symbols" in status

    def test_start_ws_feed_respects_disabled(self, monkeypatch):
        from backend.services.arbitrage import cross_exchange_ws_feed as ws_mod

        class _WsCfg:
            enabled = False
            symbols = ["BTC"]
            poll_interval_sec = 2.0

        class _Cfg:
            ws_feed = _WsCfg()

        monkeypatch.setattr(
            "backend.config.arb_config_loader.arb_config",
            _Cfg(),
        )
        assert ws_mod.start_ws_feed() is False
