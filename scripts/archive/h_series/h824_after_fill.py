# -*- coding: utf-8 -*-
"""成交之后再盯市，而不是用窗口结束时的中间价。

挂卖一的成交时刻 τ = 窗口内第一笔价格 >= 当时卖一的成交。
做空盈亏 Y(h) = (卖一 - τ 之后 h 秒的中间价) / 卖一 × 10000。
h=0 只剩价差。h>0 才是进场以后价格有没有往有利方向走。
"""
from __future__ import annotations

import io
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker.attribution import _market_dsn  # noqa: E402
import psycopg  # noqa: E402

MARKS_S = (0, 5, 15, 30, 60, 90)


def load(conn, sym, lo_ms):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT event_ts_ms, bid_px, ask_px FROM asterdex_book_ticker"
            " WHERE symbol=%s AND event_ts_ms>%s AND bid_px>0 AND ask_px>bid_px"
            " ORDER BY event_ts_ms",
            (sym + "USDT", lo_ms),
        )
        bk = cur.fetchall()
        cur.execute(
            "SELECT event_ts_ms, price FROM asterdex_trades"
            " WHERE symbol=%s AND event_ts_ms>%s ORDER BY event_ts_ms",
            (sym + "USDT", lo_ms),
        )
        tr = cur.fetchall()
    bts = np.array([r[0] for r in bk], dtype=np.int64)
    bid = np.array([float(r[1]) for r in bk])
    ask = np.array([float(r[2]) for r in bk])
    tts = np.array([r[0] for r in tr], dtype=np.int64)
    px = np.array([float(r[1]) for r in tr])
    return bts, bid, ask, tts, px


def mid_at(bts, mid, ts):
    i = np.searchsorted(bts, ts, side="right") - 1
    if i < 0 or i >= len(mid):
        return np.nan
    return mid[i]


def study(sym, hours=6, wait_s=30):
    lo = int((time.time() - hours * 3600) * 1000)
    with psycopg.connect(_market_dsn(), autocommit=True) as conn:
        bts, bid, ask, tts, px = load(conn, sym, lo)
    if len(bts) < 500 or len(tts) < 200:
        print(f"\n{sym}: 数据不足")
        return
    mid = (bid + ask) / 2.0
    horizon = wait_s * 1000
    grid = np.arange(int(bts[0]) + 60_000, int(bts[-1]) - 120_000, 15_000)
    idx = np.searchsorted(bts, grid, side="right") - 1
    rows = []
    for t0, i in zip(grid, idx):
        if i < 0:
            continue
        a0 = ask[i]
        b0 = bid[i]
        if a0 <= b0 or b0 <= 0:
            continue
        left = np.searchsorted(tts, t0, side="right")
        right = np.searchsorted(tts, t0 + horizon, side="right")
        if right <= left:
            continue
        sl_ts = tts[left:right]
        sl_px = px[left:right]
        sell_at = np.flatnonzero(sl_px >= a0)
        buy_at = np.flatnonzero(sl_px <= b0)
        if len(sell_at):
            tau = int(sl_ts[sell_at[0]])
            marks = {}
            for h in MARKS_S:
                m = mid_at(bts, mid, tau + h * 1000)
                marks[h] = (a0 - m) / a0 * 1e4 if m == m else np.nan
            rows.append(("sell", t0, marks))
        if len(buy_at):
            tau = int(sl_ts[buy_at[0]])
            marks = {}
            for h in MARKS_S:
                m = mid_at(bts, mid, tau + h * 1000)
                marks[h] = (m - b0) / b0 * 1e4 if m == m else np.nan
            rows.append(("buy", t0, marks))
    if not rows:
        print(f"\n{sym}: 没有成交")
        return
    # 后三分之一，按挂单时刻
    times = sorted({r[1] for r in rows})
    cut = times[len(times) * 2 // 3]
    print(f"\n{sym}  成交后盯市（后 1/3，等成交最多 {wait_s}s）")
    print(f"  {'边':4} {'h':>4} {'n':>5} {'平均':>8} {'中位':>8}")
    for side in ("sell", "buy"):
        part = [r for r in rows if r[0] == side and r[1] >= cut]
        for h in MARKS_S:
            xs = np.array([r[2][h] for r in part], dtype=float)
            xs = xs[np.isfinite(xs)]
            if len(xs) == 0:
                continue
            flag = "  <--" if h == 0 else ""
            print(f"  {side:4} {h:4d}s {len(xs):5d} {xs.mean():+8.2f} {np.median(xs):+8.2f}{flag}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    for s in ("PLAY", "LYN", "BTC", "ETH"):
        study(s)
