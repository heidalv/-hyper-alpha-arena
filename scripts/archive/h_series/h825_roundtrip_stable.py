# -*- coding: utf-8 -*-
"""成交之后的盈亏再拆三层：时间是否稳定、左尾有多大、买回来还剩多少。

做空：卖在当时卖一。真正买回是之后挂在买一，并且买一被打到。
盯市到中间价会把还没买回的价差也算成利润。
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


def first_cross(tts, px, t0, t1, price, side):
    left = np.searchsorted(tts, t0, side="right")
    right = np.searchsorted(tts, t1, side="right")
    if right <= left:
        return None
    sl_ts, sl_px = tts[left:right], px[left:right]
    hit = sl_px >= price if side == "sell" else sl_px <= price
    idx = np.flatnonzero(hit)
    if len(idx) == 0:
        return None
    return int(sl_ts[idx[0]])


def mid_bid_at(bts, bid, ask, ts):
    i = np.searchsorted(bts, ts, side="right") - 1
    if i < 0 or i >= len(bts):
        return np.nan, np.nan, np.nan
    return (bid[i] + ask[i]) / 2.0, bid[i], ask[i]


def collect(sym, hours=12, wait_s=30, hold_s=90):
    lo = int((time.time() - hours * 3600) * 1000)
    with psycopg.connect(_market_dsn(), autocommit=True) as conn:
        bts, bid, ask, tts, px = load(conn, sym, lo)
    if len(bts) < 500 or len(tts) < 200:
        print(f"\n{sym}: 数据不足")
        return []
    horizon = wait_s * 1000
    grid = np.arange(int(bts[0]) + 60_000, int(bts[-1]) - (wait_s + hold_s) * 1000, 15_000)
    out = []
    for t0 in grid:
        i = np.searchsorted(bts, t0, side="right") - 1
        if i < 0:
            continue
        a0, b0 = ask[i], bid[i]
        if a0 <= b0 or b0 <= 0:
            continue
        tau = first_cross(tts, px, t0, t0 + horizon, a0, "sell")
        if tau is None:
            continue
        m_now, b_now, _ = mid_bid_at(bts, bid, ask, tau)
        y0 = (a0 - m_now) / a0 * 1e4 if m_now == m_now else np.nan
        m_h, bid_h, ask_h = mid_bid_at(bts, bid, ask, tau + hold_s * 1000)
        y_mid = (a0 - m_h) / a0 * 1e4 if m_h == m_h else np.nan
        y_cover = (a0 - ask_h) / a0 * 1e4 if ask_h == ask_h else np.nan
        # 挂在当时的买一上买回：成交价 <= 那一刻的买一
        y_exec = np.nan
        left = np.searchsorted(tts, tau, side="right")
        right = np.searchsorted(tts, tau + hold_s * 1000, side="right")
        for ts, price in zip(tts[left:right], px[left:right]):
            j = np.searchsorted(bts, ts, side="right") - 1
            if j < 0:
                continue
            if price <= bid[j]:
                y_exec = (a0 - bid[j]) / a0 * 1e4
                break
        spread = (a0 - b0) / ((a0 + b0) / 2.0) * 1e4
        out.append((int(t0), y0, y_mid, y_cover, y_exec, spread))
    return out


def report(sym, rows, hold_s):
    if not rows:
        return
    times = np.array([r[0] for r in rows])
    cuts = [np.quantile(times, q) for q in (0.33, 0.66)]
    folds = [
        ("前段", [r for r, t in zip(rows, times) if t <= cuts[0]]),
        ("中段", [r for r, t in zip(rows, times) if cuts[0] < t <= cuts[1]]),
        ("后段", [r for r, t in zip(rows, times) if t > cuts[1]]),
    ]
    print(f"\n{sym}  做空，成交后再拿 {hold_s}s，样本 {len(rows)}")
    print(f"  {'段':4} {'n':>5} {'刚成交':>8} {'盯中间价':>8} {'吃单买回':>8} {'挂买一买回':>10} {'买到':>6}")
    for name, part in [("全部", rows)] + folds:
        if not part:
            continue
        def col(i):
            xs = np.array([p[i] for p in part], dtype=float)
            xs = xs[np.isfinite(xs)]
            if len(xs) == 0:
                return "n/a"
            return f"{xs.mean():+.2f}"
        execs = np.array([p[4] for p in part], dtype=float)
        rate = np.isfinite(execs).mean() * 100
        print(f"  {name:4} {len(part):5d} {col(1):>8} {col(2):>8} {col(3):>8} {col(4):>10} {rate:5.0f}%")
    last = [r for r, t in zip(rows, times) if t > cuts[1]]
    ys = np.array([r[4] for r in last], dtype=float)
    ys = ys[np.isfinite(ys)]
    if len(ys):
        print(f"  后段真正买回  n={len(ys)} 中位 {np.median(ys):+.2f}  "
              f"最差10% {np.quantile(ys, 0.1):+.2f}  最好10% {np.quantile(ys, 0.9):+.2f}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    for sym, hold in (("PLAY", 30), ("PLAY", 90), ("LYN", 30), ("LYN", 90), ("BTC", 30)):
        report(sym, collect(sym, hold_s=hold), hold)
