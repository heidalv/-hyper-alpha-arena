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
    # [2026-09-24 用例口径更新] BULLA 现在**应当放行**：2026-09-18 起
    # `ai_coin_unified.filter_tradeable_ai_symbols` 去掉了「必须上 Hyperliquid」的
    # HY 时代残留（见该文件 211-215 行注释：在活跃所上架的币只按成交量判，
    # venue/rank 门槛仅用于「快照未能确认在活跃所」的币；开关 AI_COIN_REQUIRE_HYPERLIQUID）。
    # BULLA 在活跃所 binance（exchanges 含 binance）、vol=1e8 ≥ min_vol=2e6 ⇒ 放行。
    # 旧断言 `"BULLA" not in out  # no HL` 编码的是 09-18 之前的行为，已失效。
    assert "BULLA" in out  # on active venue(binance) + vol≥min_vol
    assert "DOLO" not in out  # vol<min_vol（不在活跃所时 rank 门槛才生效，此处按量踢）
    assert "TSLA" not in out


def test_market_summary_context_has_cache_ts():
    from backend.services.full_auto.market_summary_helpers import MarketSummaryContext

    ctx = MarketSummaryContext(market_scan_cache={}, market_scan_cache_ts=123.0)
    assert ctx.market_scan_cache_ts == 123.0
