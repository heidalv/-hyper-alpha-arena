# -*- coding: utf-8 -*-
"""轮67 P0-2 回归：DataCenter 的 trade 新鲜度闸门不得误杀「有界历史区间」读取。

## 事故

`KlineResult.is_fresh` 在调用方已经传入 `start`/`end` 时，仍拿「所请求窗口内最新一根 bar」
去和 wall-clock now 比较 —— 读历史区间必然判为「过期」，于是 `purpose="trade"` 把整段清空。

实测（修复前）：

    get_klines("BTC","1d", start="2026-01-01", end="2026-03-01", purpose="trade")    → count=0
    同一调用                              purpose="research"                        → count=60

仓内已有先例记录同类损失：`scalp_signal_logger.py:207-213` 记载 57,000 条信号标签因此丢失。

## 同时锁死

- 「无界（实时）」读取**仍然**受新鲜度约束（修复不能顺手把闸门拆掉）；
- 新鲜度阈值有**唯一权威** `kline_fresh_window_sec`，三处调用方口径一致；
- `lag_sec`（采集滞后，相对 bar 收盘）与 `stale_sec`（相对 bar 开盘）口径正确区分。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.data_center import (
    KlineResult,
    PERIOD_SECONDS,
    kline_fresh_window_sec,
)


def _mk(period="1h", *, last_ts=None, closed_only=True, bounded=False, n=3):
    now = int(time.time())
    ts = last_ts if last_ts is not None else now
    rows = [{"timestamp": ts - i * PERIOD_SECONDS[period], "open": 1.0, "high": 1.0,
             "low": 1.0, "close": 1.0, "volume": 1.0} for i in range(n - 1, -1, -1)]
    r = KlineResult(symbol="BTC", period=period, exchange="binance", rows=rows)
    r.closed_only = closed_only
    r.bounded_history = bounded
    return r


# ══════════════════════════════════════════════════════════════════════
# 1. 有界历史区间：新鲜度判定不适用
# ══════════════════════════════════════════════════════════════════════

def test_bounded_history_is_always_fresh():
    """历史区间的最后一根 bar 必然「很旧」——但它不该被判过期。"""
    old = int(time.time()) - 86400 * 200      # 200 天前的 bar
    r = _mk("1d", last_ts=old, bounded=True)
    assert r.stale_sec is not None and r.stale_sec > 86400
    assert r.is_fresh is True, "有界历史区间不应受新鲜度约束"


def test_unbounded_old_data_is_not_fresh():
    """同一根旧 bar，若调用方没点名区间 → 仍然判过期（闸门没被拆掉）。"""
    old = int(time.time()) - 86400 * 200
    r = _mk("1d", last_ts=old, bounded=False)
    assert r.is_fresh is False


def test_unbounded_fresh_data_is_fresh():
    """实时读取且数据新鲜 → 通过。"""
    r = _mk("1h", last_ts=int(time.time()) - 60, bounded=False)
    assert r.is_fresh is True


def test_bounded_flag_defaults_off():
    """默认必须是不放行，避免有人忘了设它就把闸门废掉。"""
    old = int(time.time()) - 86400 * 200
    r = _mk("1d", last_ts=old)
    assert r.bounded_history is False
    assert r.is_fresh is False


# ══════════════════════════════════════════════════════════════════════
# 2. 阈值唯一权威
# ══════════════════════════════════════════════════════════════════════

def test_threshold_is_2p_plus_60():
    for p in ("15m", "1h", "4h", "1d"):
        assert kline_fresh_window_sec(p) == PERIOD_SECONDS[p] * 2.0 + 60.0


def test_threshold_is_the_one_is_fresh_uses():
    """边界：略低于阈值 → 新鲜；略高于 → 不过。

    这条断言锁死「阈值只有一处定义」：若 `is_fresh` 内部再写一个数，
    边界立刻对不上。

    注：`time.time()` 带小数，`stale_sec` 在 `__post_init__` 里算，
    与取值之间有毫秒级漂移，故留 2 秒余量而不是卡在等号上。
    """
    period = "1h"
    thr = kline_fresh_window_sec(period)

    under = _mk(period, last_ts=int(time.time() - thr + 2), bounded=False)
    assert under.is_fresh is True, "略低于阈值应视为新鲜"

    over = _mk(period, last_ts=int(time.time() - thr - 2), bounded=False)
    assert over.is_fresh is False, "略高于阈值应判过期"


def test_all_threshold_consumers_agree():
    """API 路由与 kline_data_service 用的阈值必须等于权威值。"""
    from backend.api.data_center_routes import _fresh_stale_limit_sec
    for p in ("15m", "1h", "4h", "1d"):
        assert _fresh_stale_limit_sec(p) == kline_fresh_window_sec(p), p


# ══════════════════════════════════════════════════════════════════════
# 3. stale_sec（相对开盘）与 lag_sec（相对收盘）口径区分
# ══════════════════════════════════════════════════════════════════════

def test_lag_sec_subtracts_one_period_when_closed_only():
    """已收盘 bar 的「年龄」天然含一个周期，lag_sec 应把它扣掉。"""
    period = "1h"
    P = PERIOD_SECONDS[period]
    r = _mk(period, last_ts=int(time.time()) - P, closed_only=True, bounded=False)
    assert r.stale_sec is not None
    assert r.lag_sec is not None
    assert abs((r.stale_sec - r.lag_sec) - P) < 2.0


def test_lag_sec_equals_stale_when_not_closed_only():
    period = "1h"
    r = _mk(period, last_ts=int(time.time()) - 300, closed_only=False, bounded=False)
    assert r.lag_sec == r.stale_sec


def test_lag_sec_none_without_rows():
    r = KlineResult(symbol="BTC", period="1h", exchange="binance", rows=[])
    assert r.stale_sec is None
    assert r.lag_sec is None
