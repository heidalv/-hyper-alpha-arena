# -*- coding: utf-8 -*-
"""切页数据要等数秒：热接口不能在请求线程里打网络，也不能每次轮询都扫账本。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _fn(src: str, name: str) -> str:
    start = src.find(f"def {name}(")
    assert start > 0, name
    rest = src[start + 1:]
    nxt = rest.find("\ndef ")
    return src[start:start + 1 + (nxt if nxt > 0 else len(rest))]


def test_ticker_bar_request_path_does_not_block_on_network():
    src = (ROOT / "backend/api/market_data_routes.py").read_text(encoding="utf-8")
    fn = _fn(src, "get_ticker_bar")
    assert "_DC_BINANCE_ALL_CACHE.get" in fn
    assert "_dc_binance_all_prices()" not in fn
    assert "_dc_all_prices()" not in fn
    assert "_dc_ticker_stats()" not in fn


def test_hft_hot_reads_are_cached():
    src = (ROOT / "backend/api/hft_routes.py").read_text(encoding="utf-8")
    assert '_read_cache_get("hero"' in src
    assert '_read_cache_get("account"' in src
    assert '_read_cache_get(cache_key, 2.0)' in src
    fee = _fn(src, "_fee_since")
    assert "_read_cache_get(cache_key, 15.0)" in fee
