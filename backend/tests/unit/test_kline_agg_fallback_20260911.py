# -*- coding: utf-8 -*-
"""[2026-09-11] KlineAgg 基准所过期 → 单所回退契约测试。

现场（9/11）：AVAX/XRP/UNI 15m~1d @binance 基准所过期、asterdex 新鲜，
整体拒绝使多头闸/regime 门对滞后币形同虚设（《长边两闸互斥与出场裁量》§5）。

契约：
- 基准所过期 + 某聚合所新鲜 → 回退该所（OHLC 单所同源、price_source 标记、
  WARNING 可见）；回退所的 volume 不重复累加。
- 全部所过期 → 仍拒绝返回空（fail-closed 不回退）。
- KLINE_AGG_BASELINE_FALLBACK_ENABLED=false → 旧口径（直接拒绝）。
"""
from __future__ import annotations

import time

import pytest


def _rows(ts_list, volume, open_base=100.0):
    out = []
    for i, ts in enumerate(ts_list):
        out.append({
            "timestamp": ts,
            "datetime": f"d{ts}",
            "open": open_base + i,
            "high": open_base + 1 + i,
            "low": open_base - 1 + i,
            "close": open_base + 0.5 + i,
            "volume": volume,
        })
    return out


def _get_service(monkeypatch, per_exchange):
    from backend.services.kline_data_service import KlineDataService
    from backend.services.kline_data_service import _KLINE_AGG_CACHE

    _KLINE_AGG_CACHE.clear()
    monkeypatch.setattr(
        "backend.services.kline_data_service.get_active_exchange",
        lambda: "binance",
    )
    monkeypatch.setenv("KLINE_VOLUME_AGGREGATION_ENABLED", "true")
    monkeypatch.setenv("KLINE_AGG_FRESHNESS_GATE_ENABLED", "true")
    monkeypatch.setenv("KLINE_AGG_BASELINE_FALLBACK_ENABLED", "true")
    svc = KlineDataService.__new__(KlineDataService)

    def fake_query(symbol, period, count, exchange):
        return per_exchange.get(exchange, [])

    monkeypatch.setattr(svc, "_query_klines_from_db", fake_query)
    return svc


def test_stale_baseline_falls_back_to_fresh_venue(monkeypatch):
    stale_ts = int(time.time()) - 10 * 3600          # binance 全过期（4h 阈值 ~8h）
    fresh_ts = int(time.time()) - 60                 # asterdex/okx 新鲜
    svc = _get_service(monkeypatch, {
        "binance": _rows([stale_ts, stale_ts - 14400], volume=10, open_base=900),
        "asterdex": _rows([fresh_ts], volume=20, open_base=100),
        "okx": _rows([fresh_ts], volume=30, open_base=500),
    })
    out = svc.get_aggregated_klines("AVAX", "4h", count=10)
    assert len(out) == 1
    assert out[0]["open"] == 100.0, "OHLC 应来自回退所 asterdex"
    assert out[0]["price_source"] == "asterdex"
    # volume：asterdex 基源 20 + okx 30 = 50（binance 无同时间戳 bar，不合并）
    assert out[0]["volume"] == pytest.approx(50.0)
    assert out[0]["volume_sources"] == 2


def test_all_venues_stale_still_rejected(monkeypatch):
    stale_ts = int(time.time()) - 10 * 3600
    svc = _get_service(monkeypatch, {
        "binance": _rows([stale_ts], volume=10),
        "asterdex": _rows([stale_ts], volume=20),
        "okx": _rows([stale_ts], volume=30),
    })
    assert svc.get_aggregated_klines("AVAX", "4h", count=10) == []


def test_rollback_flag_restores_reject(monkeypatch):
    stale_ts = int(time.time()) - 10 * 3600
    fresh_ts = int(time.time()) - 60
    svc = _get_service(monkeypatch, {
        "binance": _rows([stale_ts], volume=10),
        "asterdex": _rows([fresh_ts], volume=20),
    })
    monkeypatch.setenv("KLINE_AGG_BASELINE_FALLBACK_ENABLED", "false")
    assert svc.get_aggregated_klines("AVAX", "4h", count=10) == []
