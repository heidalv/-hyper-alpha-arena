"""多所成交量聚合（读侧）单元测试。"""
from __future__ import annotations

import pytest


def _rows(ts_list, volume, extra=None):
    out = []
    for i, ts in enumerate(ts_list):
        row = {
            "timestamp": ts,
            "datetime": f"d{ts}",
            "open": 100.0 + i,
            "high": 101.0 + i,
            "low": 99.0 + i,
            "close": 100.5 + i,
            "volume": volume,
        }
        if extra:
            row.update(extra)
        out.append(row)
    return out


def _get_service(monkeypatch, per_exchange):
    from backend.services.kline_data_service import KlineDataService
    from backend.services.kline_data_service import _KLINE_AGG_CACHE

    _KLINE_AGG_CACHE.clear()
    monkeypatch.setattr(
        "backend.services.kline_data_service.get_active_exchange",
        lambda: "asterdex",
    )
    monkeypatch.setenv("KLINE_VOLUME_AGGREGATION_ENABLED", "true")
    # [2026-08-15 R4] 聚合新鲜度门默认开；本组测试用 1970 年合成时间戳
    # 验证聚合逻辑本身，关闭新鲜度门（新鲜度拒绝见专项测试）。
    monkeypatch.setenv("KLINE_AGG_FRESHNESS_GATE_ENABLED", "false")
    svc = KlineDataService.__new__(KlineDataService)

    def fake_query(symbol, period, count, exchange):
        return per_exchange.get(exchange, [])

    monkeypatch.setattr(svc, "_query_klines_from_db", fake_query)
    return svc


def test_aggregates_volume_keeps_base_ohlc(monkeypatch):
    svc = _get_service(
        monkeypatch,
        {
            "asterdex": _rows([1000, 2000], volume=10),
            "binance": _rows([1000, 2000], volume=20),
            "okx": _rows([1000, 2000], volume=30),
        },
    )
    out = svc.get_aggregated_klines("BTC", "4h", count=10)
    assert len(out) == 2
    assert out[0]["open"] == 100.0
    assert out[0]["volume"] == pytest.approx(60.0)
    assert out[0]["volume_sources"] == 3
    assert out[1]["volume"] == pytest.approx(60.0)


def test_aligns_by_timestamp_and_skips_missing(monkeypatch):
    svc = _get_service(
        monkeypatch,
        {
            "asterdex": _rows([1000, 2000], volume=10),
            "binance": _rows([1000], volume=20),  # 缺 2000
            "okx": _rows([2000], volume=30),     # 缺 1000
        },
    )
    out = svc.get_aggregated_klines("BTC", "4h", count=10)
    assert out[0]["volume"] == pytest.approx(30.0)
    assert out[1]["volume"] == pytest.approx(40.0)


def test_exchange_query_error_is_skipped(monkeypatch):
    svc = _get_service(
        monkeypatch,
        {
            "asterdex": _rows([1000], volume=10),
            "binance": _rows([1000], volume=20),
        },
    )

    def fake_query(symbol, period, count, exchange):
        if exchange == "okx":
            raise RuntimeError("rate limited")
        return {"asterdex": _rows([1000], 10), "binance": _rows([1000], 20)}[exchange]

    monkeypatch.setattr(svc, "_query_klines_from_db", fake_query)
    out = svc.get_aggregated_klines("BTC", "4h", count=10)
    assert out[0]["volume"] == pytest.approx(30.0)


def test_disabled_flag_falls_back_to_single_exchange(monkeypatch):
    from backend.services.kline_data_service import KlineDataService
    from backend.services.kline_data_service import _KLINE_AGG_CACHE

    _KLINE_AGG_CACHE.clear()
    monkeypatch.setenv("KLINE_VOLUME_AGGREGATION_ENABLED", "false")
    monkeypatch.setattr(
        "backend.services.kline_data_service.get_active_exchange",
        lambda: "asterdex",
    )
    svc = KlineDataService.__new__(KlineDataService)
    monkeypatch.setattr(
        svc,
        "get_klines_from_db",
        lambda symbol, period, count=500, exchange=None: _rows([1000], volume=10),
    )
    out = svc.get_aggregated_klines("BTC", "4h", count=10)
    assert out[0]["volume"] == pytest.approx(10.0)
    assert "volume_sources" not in out[0]


def test_no_base_rows_returns_empty(monkeypatch):
    svc = _get_service(monkeypatch, {"asterdex": [], "binance": _rows([1000], 20)})
    assert svc.get_aggregated_klines("BTC", "4h", count=10) == []


def test_stale_base_rows_rejected_when_gate_on(monkeypatch):
    """[2026-08-15 R4] 新鲜度门开启时，基准所最新 bar 过期 → 拒绝返回空
    （与 data_center purpose=trade 的 is_fresh 同口径：period*2+60）。"""
    import time

    from backend.services.kline_data_service import KlineDataService
    from backend.services.kline_data_service import _KLINE_AGG_CACHE

    _KLINE_AGG_CACHE.clear()
    monkeypatch.setattr(
        "backend.services.kline_data_service.get_active_exchange",
        lambda: "asterdex",
    )
    monkeypatch.setenv("KLINE_VOLUME_AGGREGATION_ENABLED", "true")
    monkeypatch.setenv("KLINE_AGG_FRESHNESS_GATE_ENABLED", "true")
    svc = KlineDataService.__new__(KlineDataService)

    stale_ts = int(time.time()) - 10 * 3600  # 10 小时前，4h 阈值 = 8h+60s
    monkeypatch.setattr(
        svc, "_query_klines_from_db",
        lambda symbol, period, count, exchange: _rows([stale_ts], volume=10),
    )
    assert svc.get_aggregated_klines("BTC", "4h", count=10) == []


def test_fresh_base_rows_pass_when_gate_on(monkeypatch):
    """新鲜度门开启时，最新 bar 新鲜 → 正常聚合返回。"""
    import time

    from backend.services.kline_data_service import KlineDataService
    from backend.services.kline_data_service import _KLINE_AGG_CACHE

    _KLINE_AGG_CACHE.clear()
    monkeypatch.setattr(
        "backend.services.kline_data_service.get_active_exchange",
        lambda: "asterdex",
    )
    monkeypatch.setenv("KLINE_VOLUME_AGGREGATION_ENABLED", "true")
    monkeypatch.setenv("KLINE_AGG_FRESHNESS_GATE_ENABLED", "true")
    svc = KlineDataService.__new__(KlineDataService)

    fresh_ts = int(time.time()) - 60  # 60 秒前，远在 4h 阈值内
    monkeypatch.setattr(
        svc, "_query_klines_from_db",
        lambda symbol, period, count, exchange: _rows([fresh_ts], volume=10),
    )
    out = svc.get_aggregated_klines("BTC", "4h", count=10)
    # 假查询对所有聚合所返回同样的 10 volume → 5 所合计 50
    assert len(out) == 1
    assert out[0]["volume"] == pytest.approx(50.0)
    assert out[0]["volume_sources"] == 5
