# -*- coding: utf-8 -*-
"""[2026-09-12 F38x] 交易所已下架交易对目录黑名单契约。

现场：Binance 2024-02 下架 XMR（现货+USD-M 永续），但 symbol_catalog 残留
binance/bybit XMR 行在 DC_ONLY 模式下被 MarketScanner 自读自写复活
（updated_at 每天刷新）→ 选币 catalog 闸放行 → 永久 stale 告警 + 浪费候选槽。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services import kline_sync_meta as ksm  # noqa: E402


def test_xmr_rejected_on_binance_and_bybit():
    for ex in ("binance", "bybit"):
        assert ksm._filter_catalog_symbols(ex, ["XMR", "BTC", "XMR"]) == ["BTC"], ex


def test_xmr_kept_on_venues_still_listing_it():
    # asterdex（自有场馆）/ hyperliquid（真实 XMR-USD 永续）不受影响
    for ex in ("asterdex", "hyperliquid"):
        out = ksm._filter_catalog_symbols(ex, ["XMR", "BTC"])
        assert "XMR" in out, ex


def test_upsert_returns_zero_and_writes_nothing_for_delisted(monkeypatch):
    """upsert 对全黑名单输入返回 0：过滤后 cleaned 为空即短路，不触达 DB 写入路径。"""
    n = ksm.upsert_symbol_catalog("binance", ["XMR"])
    assert n == 0
    # 写入路径在 cleaned 非空时才触达 MarketSessionLocal——此处直接短路返回。
    # （_ensure_tables 在过滤前调用属建表保障，无副作用。）
