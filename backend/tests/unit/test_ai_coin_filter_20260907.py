# -*- coding: utf-8 -*-
from __future__ import annotations

from backend.services.ai_coin_unified import filter_tradeable_ai_symbols, _HARD_DENY_SYMBOLS


def test_hard_deny_stocks(monkeypatch):
    # [2026-09-11 确定性修复] 原用例直连真实流动性快照：实盘 DOGE rank>120 会被
    # 质量闸踢出（logs 实证 "DOGE(rank>120)"），用例随行情飘红。注入确定性快照。
    import backend.services.ai_coin_unified as U

    monkeypatch.setattr(
        U,
        "_liquidity_snapshot",
        lambda: {
            "BTC": {"volume_24h": 1e10, "exchanges": ["binance"], "rank": 1},
            "ETH": {"volume_24h": 9e9, "exchanges": ["binance"], "rank": 2},
            "DOGE": {"volume_24h": 5e8, "exchanges": ["binance"], "rank": 7},
        },
    )
    monkeypatch.setattr(
        U,
        "_kline_ages_sec",
        lambda syms: {s: 60.0 for s in syms},
    )
    monkeypatch.setattr(
        U,
        "_ai_quality_thresholds",
        lambda: {
            "min_vol": 2_000_000.0,
            "require_hl": 1.0,
            "max_age_sec": 21600.0,
            "max_rank": 120,
        },
    )
    monkeypatch.setattr(
        "backend.services.market_scanner.MarketScanner.get_all_tradable_symbols",
        staticmethod(lambda _ex: ["BTC", "ETH", "DOGE"]),
    )
    monkeypatch.setattr(
        "backend.services.exchange_config.get_active_exchange",
        lambda: "binance",
    )
    out = filter_tradeable_ai_symbols(["BTC", "TSLA", "ETH", "EWY", "DOGE"])
    assert "TSLA" not in out and "EWY" not in out
    assert "BTC" in out and "ETH" in out and "DOGE" in out


def test_hard_deny_set_covers_common_equities():
    assert "TSLA" in _HARD_DENY_SYMBOLS
    assert "SPY" in _HARD_DENY_SYMBOLS
    assert "SKHYNIX" in _HARD_DENY_SYMBOLS
    assert "SOXL" in _HARD_DENY_SYMBOLS


def test_quality_gate_drops_no_hl_and_illiquid(monkeypatch):
    """无 Hyperliquid / 低成交额 / 停采 → 踢出 AI 池。"""
    import backend.services.ai_coin_unified as U

    monkeypatch.setattr(
        U,
        "_liquidity_snapshot",
        lambda: {
            "BTC": {"volume_24h": 1e10, "exchanges": ["binance", "hyperliquid"], "rank": 1},
            "BULLA": {"volume_24h": 1e8, "exchanges": ["binance", "asterdex"], "rank": 19},
            "DOLO": {"volume_24h": 1e5, "exchanges": ["binance"], "rank": 600},
            "DOGE": {"volume_24h": 5e8, "exchanges": ["binance", "hyperliquid"], "rank": 7},
        },
    )
    monkeypatch.setattr(
        U,
        "_kline_ages_sec",
        lambda syms: {s: 60.0 for s in syms},
    )
    monkeypatch.setattr(
        U,
        "_ai_quality_thresholds",
        lambda: {
            "min_vol": 2_000_000.0,
            "require_hl": 1.0,
            "max_age_sec": 21600.0,
            "max_rank": 120,
        },
    )
    monkeypatch.setattr(
        "backend.services.market_scanner.MarketScanner.get_all_tradable_symbols",
        staticmethod(lambda _ex: ["BTC", "BULLA", "DOLO", "DOGE", "ETH"]),
    )
    monkeypatch.setattr(
        "backend.services.exchange_config.get_active_exchange",
        lambda: "binance",
    )

    out = filter_tradeable_ai_symbols(["BTC", "BULLA", "DOLO", "DOGE", "TSLA"])
    assert "BTC" in out and "DOGE" in out
    assert "BULLA" not in out  # no HL
    assert "DOLO" not in out  # low vol + rank
    assert "TSLA" not in out


def test_market_summary_context_has_cache_ts():
    from backend.services.full_auto.market_summary_helpers import MarketSummaryContext

    ctx = MarketSummaryContext(market_scan_cache={}, market_scan_cache_ts=123.0)
    assert ctx.market_scan_cache_ts == 123.0
