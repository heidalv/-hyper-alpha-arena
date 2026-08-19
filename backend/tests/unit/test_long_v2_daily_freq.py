# -*- coding: utf-8 -*-
"""A1: 长线 V2 日频管理——丢弃未收盘 bar + 分类缓存。"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import datetime
import numpy as np
import pandas as pd

from backend.services import long_trend_v2 as lv2


def _mk_rows(n=300, last_ts=None):
    """构造 n 根 1d K 线 rows（dict 列表，timestamp 为 epoch 秒）。"""
    import time
    day = 86400
    base = (last_ts if last_ts is not None else time.time()) - (n - 1) * day
    rows = []
    rng = np.random.default_rng(1)
    close = 100.0
    for i in range(n):
        close = close * (1 + rng.normal(0, 0.02))
        rows.append({
            "timestamp": float(base + i * day),
            "open": close * 0.99, "high": close * 1.03,
            "low": close * 0.97, "close": close,
        })
    return rows


def test_live_1d_drops_unclosed_bar():
    """最后一根 ts=今天00:00UTC（未收盘）→ 被丢弃；分类只用已收盘数据。"""
    today = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = _mk_rows(n=300, last_ts=today.timestamp())
    with patch("backend.services.kline_data_service.kline_service.get_klines_from_db",
               return_value=rows):
        df = lv2._live_1d("BTC")
    assert df is not None and len(df) == 299
    last = float(df["timestamp"].iloc[-1])
    assert last < today.timestamp(), "最后一根应为已收盘 bar（ts < 今天00:00 UTC）"


def test_live_1d_keeps_closed_bar():
    """最后一根 ts=昨天（已收盘）→ 不丢弃。"""
    today = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = _mk_rows(n=300, last_ts=today.timestamp() - 86400)
    with patch("backend.services.kline_data_service.kline_service.get_klines_from_db",
               return_value=rows):
        df = lv2._live_1d("BTC")
    assert df is not None and len(df) == 300


def test_l1_classification_cache():
    """同一已收盘 bar 内重复调用命中缓存：classify 只被调用一次。"""
    today = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = _mk_rows(n=300, last_ts=today.timestamp() - 86400)
    lv2._L1_CACHE.clear()
    calls = {"n": 0}
    real_classify = lv2.classify

    def _fake_classify(df):
        calls["n"] += 1
        return real_classify(df)

    with patch("backend.services.kline_data_service.kline_service.get_klines_from_db",
               return_value=rows), \
         patch("backend.services.long_trend_v2.classify", side_effect=_fake_classify):
        df1, c1 = lv2._get_l1_classification("BTC")
        df2, c2 = lv2._get_l1_classification("BTC")
    assert c1 is not None and c2 is not None
    assert calls["n"] == 1, f"缓存应命中，classify 只调 1 次，实际 {calls['n']}"
    assert c1["score"] == c2["score"]
    lv2._L1_CACHE.clear()
