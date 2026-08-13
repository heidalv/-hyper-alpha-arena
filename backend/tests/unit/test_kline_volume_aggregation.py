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
