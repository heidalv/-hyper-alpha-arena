"""数据源抽象层单测（v6 阶段 2 · 2.3：DataProvider / Coinglass 免费→付费无缝切换 + 链路健康）。"""
import time

import pytest

from backend.services.data.data_provider import (
    CoinglassProvider,
    DataProvider,
    LiquidationData,
    ProviderChain,
    coinglass_provider,
    provider_chain,
)
from backend.services.data_quality_monitor import DataQualityMonitor


# ─────────────────────────── Provider 契约与 tier ───────────────────────────

def test_provider_contract():
    """DataProvider 抽象契约：tier + 四个 fetch_* + health。"""
    p = coinglass_provider
    assert isinstance(p, DataProvider)
    assert p.tier in ("free", "paid")
    for m in ("fetch_funding", "fetch_liquidation", "fetch_netflow", "fetch_stablecoin_mint", "health"):
        assert callable(getattr(p, m))


def test_tier_detection(monkeypatch):
    """tier 判定：无 key → free；有付费 key → paid。"""
    monkeypatch.delenv("COINGLASS_API_KEY", raising=False)
    monkeypatch.delenv("COINGLASS_FREE_API_KEY", raising=False)
    p = CoinglassProvider()
    assert p.tier == "free"
    assert not p.has_key

    monkeypatch.setenv("COINGLASS_API_KEY", "paid-key-123")
    p2 = CoinglassProvider()
    assert p2.tier == "paid"
    assert p2.has_key


def test_set_api_key_hot_switch():
    """set_api_key 运行时热切换：free → paid 无缝升级。"""
    p = CoinglassProvider()
    p._paid_key = ""
    p._free_key = "free-key"
    p.tier = "free"
    p.set_api_key("paid-key-999", tier="paid")
    assert p.tier == "paid"
    assert p._paid_key == "paid-key-999"
    # headers 携带付费 key
    h = p._headers()
    assert h.get("cg-api-key") == "paid-key-999"


# ─────────────────────────── fetch_* 解析与容错 ───────────────────────────

def test_fetch_funding_parses(monkeypatch):
    p = CoinglassProvider()
    monkeypatch.setattr(p, "_get_json", lambda path, params=None: {
        "data": [{"fundingRate": 0.000125, "time": 1}]
    })
    assert p.fetch_funding("BTC") == pytest.approx(0.000125)
    assert p.stats.success_calls == 1 and p.stats.total_calls == 1
    assert p.stats.last_success > 0


def test_fetch_liquidation_aggregates(monkeypatch):
    p = CoinglassProvider()
    rows = [
        {"longVolUsd": 1e6, "shortVolUsd": 2e6},
        {"longVolUsd": 3e6, "shortVolUsd": 4e6},
    ]
    monkeypatch.setattr(p, "_get_json", lambda path, params=None: {"data": rows})
    liq = p.fetch_liquidation("BTC")
    assert isinstance(liq, LiquidationData)
    assert liq.long_usd == pytest.approx(4e6)
    assert liq.short_usd == pytest.approx(6e6)
    assert liq.total_usd == pytest.approx(1e7)


def test_fetch_netflow_delta(monkeypatch):
    p = CoinglassProvider()
    rows = [{"balance": 100.0}, {"balance": 160.0}]
    monkeypatch.setattr(p, "_get_json", lambda path, params=None: {"data": rows})
    assert p.fetch_netflow("BTC") == pytest.approx(60.0)  # 最近 − 窗口起点


def test_fetch_failure_degrades_gracefully(monkeypatch):
    """_get_json 抛异常 → 返回 None、记录 last_error、不向调用方抛出。"""
    p = CoinglassProvider()

    def _boom(path, params=None):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(p, "_get_json", _boom)
    assert p.fetch_funding("BTC") is None
    assert p.fetch_liquidation("BTC") is None
    assert p.fetch_netflow("BTC") is None
    assert p.stats.total_calls == 3
    assert p.stats.success_calls == 0
    assert "rate limited" in p.stats.last_error
    # health 视图：0% 成功率 → 不健康
    assert p.health()["ok"] is False


def test_fetch_empty_rows_none(monkeypatch):
    p = CoinglassProvider()
    monkeypatch.setattr(p, "_get_json", lambda path, params=None: {"data": []})
    assert p.fetch_funding("BTC") is None


def test_stablecoin_mint_unavailable_returns_none(monkeypatch):
    """稳定币端点未配置 → None（不阻塞）；配置 COINGLASS_STABLE_URI → 解析。"""
    p = CoinglassProvider()
    monkeypatch.delenv("COINGLASS_STABLE_URI", raising=False)
    assert p.fetch_stablecoin_mint("USDT") is None

    monkeypatch.setenv("COINGLASS_STABLE_URI", "/api/stablecoin/mint-burn")
    p2 = CoinglassProvider()
    monkeypatch.setattr(p2, "_get_json", lambda path, params=None: {
        "data": [{"mint": 5e8, "burn": 2e8}]
    })
    assert p2.fetch_stablecoin_mint("USDT") == pytest.approx(3e8)


# ─────────────────────────── ProviderChain 降级 ───────────────────────────

def test_chain_fallback_to_secondary(monkeypatch):
    """primary 失败 → 自动切 secondary；secondary 成功即返回。"""
    class _P1(DataProvider):
        name = "p1"
        def fetch_funding(self, symbol):
            return None  # 失败
        def fetch_liquidation(self, symbol):
            return None
        def fetch_netflow(self, asset):
            return None
        def fetch_stablecoin_mint(self, asset):
            return None

    class _P2(DataProvider):
        name = "p2"
        def fetch_funding(self, symbol):
            return 0.0001
        def fetch_liquidation(self, symbol):
            return None
        def fetch_netflow(self, asset):
            return None
        def fetch_stablecoin_mint(self, asset):
            return None

    chain = ProviderChain([_P1(), _P2()])
    assert chain.fetch("fetch_funding", "BTC") == pytest.approx(0.0001)
    assert chain.fetch("fetch_liquidation", "BTC") is None


def test_chain_health_report():
    report = provider_chain.health_report()
    assert "coinglass" in report
    assert report["coinglass"]["tier"] in ("free", "paid")


# ─────────────────────────── 链路健康监控 ───────────────────────────

def test_check_link_gaps_detects_stale_source():
    dq = DataQualityMonitor()
    # 行情源：5min 前成功 → 超 MARKET_GAP_SEC(300) → 告警
    dq.record_source_call("ticker_asterdex", success=True, latency_ms=10)
    dq._source_health["ticker_asterdex"].last_success = time.time() - 400
    alerts = dq.check_link_gaps()
    assert any(a.source == "link_gap" and "market" in a.message for a in alerts)

    # 新鲜源不告警
    dq2 = DataQualityMonitor()
    dq2.record_source_call("ticker_asterdex", success=True)
    dq2.record_source_call("onchain_netflow", success=True)
    assert dq2.check_link_gaps() == []


def test_check_link_gaps_onchain_threshold():
    """链上源 1h 阈值：40min 不算缺口，2h 算。"""
    dq = DataQualityMonitor()
    dq.record_source_call("onchain_netflow", success=True)
    dq._source_health["onchain_netflow"].last_success = time.time() - 2400  # 40min
    assert dq.check_link_gaps() == []

    dq2 = DataQualityMonitor()
    dq2.record_source_call("onchain_netflow", success=True)
    dq2._source_health["onchain_netflow"].last_success = time.time() - 7200  # 2h
    alerts = dq2.check_link_gaps()
    assert any(a.source == "link_gap" and "onchain" in a.message for a in alerts)


def test_get_link_health_classification():
    """三链路归类：ticker→market、kline→kline、netflow/funding→onchain。"""
    dq = DataQualityMonitor()
    dq.record_source_call("ticker_asterdex", success=True)
    dq.record_source_call("kline_p0_asterdex", success=True)
    dq.record_source_call("onchain_netflow", success=True)
    dq.record_source_call("coinglass_funding", success=True)
    dq.record_source_call("unknown_src", success=True)  # 不归类

    lh = dq.get_link_health()
    assert {s["name"] for s in lh["market"]["sources"]} == {"ticker_asterdex"}
    assert {s["name"] for s in lh["kline"]["sources"]} == {"kline_p0_asterdex"}
    assert {s["name"] for s in lh["onchain"]["sources"]} == {"onchain_netflow", "coinglass_funding"}
    assert lh["market"]["status"] == "ok"
    assert "providers" in lh and "coinglass" in lh["providers"]

    # 无记录链路 → n/a
    lh2 = DataQualityMonitor().get_link_health()
    assert lh2["market"]["status"] == "n/a"


# ─────────────────────────── 生产路径集成 ───────────────────────────

def test_onchain_collector_fills_exchange_net_flow(monkeypatch):
    """onchain_data_collector：Coinglass 有 key 时 collect_all 产出 exchange_net_flow。"""
    from backend.services import onchain_data_collector as oc_mod
    coll = oc_mod.OnchainDataCollector()

    class _FakeProvider:
        has_key = True
        stats = coinglass_provider.stats

        def fetch_netflow(self, asset):
            return {"BTC": 12e6, "ETH": -3e6}[asset]

        def fetch_stablecoin_mint(self, asset):
            return 45e6

    monkeypatch.setattr("backend.services.data.data_provider.get_coinglass_provider",
                        lambda: _FakeProvider())
    # 屏蔽真实网络源
    monkeypatch.setattr(coll, "_collect_tvl", lambda symbol: 0.0)
    monkeypatch.setattr(coll, "_collect_macro", lambda: {"fear_greed": 50, "btc_dominance": 55.0})
    monkeypatch.setattr(coll, "_collect_blockchain_info", lambda: {})
    monkeypatch.setattr(coll, "_collect_mempool", lambda: {})
    monkeypatch.setattr(coll, "_collect_etherscan", lambda: {})

    out = coll.collect_all(["BTC", "ETH"])
    assert out["BTC"]["exchange_net_flow"] == pytest.approx(12e6)
    assert out["ETH"]["exchange_net_flow"] == pytest.approx(-3e6)
    assert out["stablecoin_mint_burn"] == pytest.approx(45e6)


def test_onchain_collector_no_key_skips(monkeypatch):
    """无 Coinglass key → collect_all 不产出链上字段（与接入前行为一致）。"""
    from backend.services import onchain_data_collector as oc_mod
    coll = oc_mod.OnchainDataCollector()

    class _NoKeyProvider:
        has_key = False

    monkeypatch.setattr("backend.services.data.data_provider.get_coinglass_provider",
                        lambda: _NoKeyProvider())
    monkeypatch.setattr(coll, "_collect_tvl", lambda symbol: 0.0)
    monkeypatch.setattr(coll, "_collect_macro", lambda: {"fear_greed": 50, "btc_dominance": 55.0})
    monkeypatch.setattr(coll, "_collect_blockchain_info", lambda: {})
    monkeypatch.setattr(coll, "_collect_mempool", lambda: {})
    monkeypatch.setattr(coll, "_collect_etherscan", lambda: {})

    out = coll.collect_all(["BTC"])
    assert "exchange_net_flow" not in out["BTC"]
    assert "stablecoin_mint_burn" not in out


def test_derivatives_analytics_coinglass_layer(monkeypatch):
    """derivatives_analytics Layer 5：Coinglass 补 funding + 清算，source 记录含 coinglass。"""
    from backend.services.derivatives_analytics_service import DerivativesAnalyticsService
    svc = DerivativesAnalyticsService()

    class _FakeProvider:
        has_key = True
        stats = coinglass_provider.stats

        def fetch_funding(self, symbol):
            return 0.000123

        def fetch_liquidation(self, symbol):
            return LiquidationData(long_usd=3e6, short_usd=7e6, total_usd=1e7)

    monkeypatch.setattr("backend.services.data.data_provider.get_coinglass_provider",
                        lambda: _FakeProvider())
    monkeypatch.setattr(svc, "_coinglass_available", lambda: True)
    # 屏蔽其他层网络调用 + DC_ONLY（默认 true 会跳过 Layer 2-5）
    monkeypatch.setattr("backend.services.market_data._dc_only_enabled", lambda: False)
    monkeypatch.setattr(svc, "_fill_from_local", lambda snap: False)
    monkeypatch.setattr(svc, "_fill_from_hyperliquid", lambda snap: False)
    monkeypatch.setattr(svc, "_fill_from_binance", lambda snap: False)
    monkeypatch.setattr(svc, "_fill_from_coinalyze", lambda snap: False)
    monkeypatch.setattr(svc, "_compute_oi_change_from_raw", lambda symbol, oi: 0.0)
    monkeypatch.setattr(svc, "_infer_price_direction", lambda symbol: "flat")

    snap = svc._build_snapshot("BTC")
    assert "coinglass" in snap.data_sources
    assert snap.funding_rate == pytest.approx(0.000123)
    assert snap.liquidation_1h_long == pytest.approx(3e6)
    assert snap.liquidation_1h_short == pytest.approx(7e6)
    assert snap.liquidation_ratio == pytest.approx(0.3)


def test_derivatives_coinglass_disabled_without_key(monkeypatch):
    """无 key → _coinglass_available False → Layer 5 不参与。"""
    from backend.services.derivatives_analytics_service import DerivativesAnalyticsService
    svc = DerivativesAnalyticsService()
    monkeypatch.setattr("backend.services.data.data_provider.coinglass_provider",
                        type("_NK", (), {"has_key": False})())
    assert svc._coinglass_available() is False
