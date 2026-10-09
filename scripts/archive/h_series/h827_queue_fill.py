# -*- coding: utf-8 -*-
"""挂单成交要先吃掉排在前面的数量。

做空：空仓时把卖单改到当前卖一，排在当时卖一数量的后面。
主动买把这一档买完，或者价格打穿卖一，才算卖出。
卖出之后把买单改到当前买一，同样排在买一数量后面。
买不到、对手价浮亏扣掉 4bp 后超过止损，就吃单离场。
同一时间只持有一笔。
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

TAKER_FEE_BP = 4.0
MAX_HOLD_MS = 600_000


def load(conn, sym, lo_ms):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT event_ts_ms, bid_px, bid_qty, ask_px, ask_qty"
            " FROM asterdex_book_ticker"
            " WHERE symbol=%s AND event_ts_ms>%s AND bid_px>0 AND ask_px>bid_px"
            " ORDER BY event_ts_ms",
            (sym + "USDT", lo_ms),
        )
        bk = cur.fetchall()
        cur.execute(
            "SELECT event_ts_ms, price, qty, is_buyer_maker FROM asterdex_trades"
            " WHERE symbol=%s AND event_ts_ms>%s ORDER BY event_ts_ms",
            (sym + "USDT", lo_ms),
        )
        tr = cur.fetchall()
    bts = np.array([r[0] for r in bk], dtype=np.int64)
    bid = np.array([float(r[1]) for r in bk])
    bq = np.array([float(r[2]) for r in bk])
    ask = np.array([float(r[3]) for r in bk])
    aq = np.array([float(r[4]) for r in bk])
    tts = np.array([r[0] for r in tr], dtype=np.int64)
    px = np.array([float(r[1]) for r in tr])
    qt = np.array([float(r[2]) for r in tr])
    maker_buy = np.array([bool(r[3]) for r in tr])
    return bts, bid, bq, ask, aq, tts, px, qt, maker_buy


def rolling_stop_at(bts, bid, ask):
    t = int(bts[0])
    end = int(bts[-1])
    times, mids = [], []
    while t <= end:
        i = int(np.searchsorted(bts, t, side="right") - 1)
        if i >= 0:
            mids.append((float(bid[i]) + float(ask[i])) / 2.0)
            times.append(t)
        t += 15_000
    stops = np.full(len(times), 15.0)
    for i in range(21, len(mids)):
        xs = mids[i - 20:i + 1]
        rets = [(xs[k] - xs[k - 1]) / xs[k - 1] * 1e4
                for k in range(1, len(xs)) if xs[k - 1] > 0]
        if len(rets) < 2:
            continue
        mu = sum(rets) / len(rets)
        var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
        stops[i] = min(40.0, max(15.0, 2.0 * (var ** 0.5)))
    return np.array(times, dtype=np.int64), stops


def _same(price, level):
    return abs(price - level) <= level * 1e-8


def walk(sym, front, hours=12, max_ahead_usd=None):
    """front=True：假设自己排在这一档的第一位。False：排在已挂数量的后面。

    max_ahead_usd：卖一上已经挂着的金额超过这个数，就不把卖单放上去。
    """
    lo = int((time.time() - hours * 3600) * 1000)
    with psycopg.connect(_market_dsn(), autocommit=True) as conn:
        bts, bid, bq, ask, aq, tts, px, qt, maker_buy = load(conn, sym, lo)
    if len(bts) < 500 or len(tts) < 200:
        print(f"\n{sym}: 数据不足")
        return []
    vol_ts, vol_stop = rolling_stop_at(bts, bid, ask)
    nb, nt = len(bts), len(tts)
    ib = 1
    it = int(np.searchsorted(tts, int(bts[0]), side="right"))
    flat = True
    order_px = 0.0
    ahead = 0.0
    cum = 0.0
    entry = 0.0
    entry_ts = 0
    ahead_usd = []
    trade_usd = []
    rows = []
    while ib < nb and it < nt:
        next_book = int(bts[ib])
        next_tr = int(tts[it])
        if next_book <= next_tr:
            ts = next_book
            level = float(ask[ib]) if flat else float(bid[ib])
            qty = float(aq[ib]) if flat else float(bq[ib])
            if flat and max_ahead_usd is not None and level * qty > float(max_ahead_usd):
                order_px = 0.0
                ahead = 0.0
                cum = 0.0
                ib += 1
                continue
            if order_px <= 0 or not _same(level, order_px):
                order_px = level
                ahead = 0.0 if front else max(qty, 0.0)
                cum = 0.0
            if not flat:
                iv = int(np.searchsorted(vol_ts, ts, side="right") - 1)
                stop = float(vol_stop[iv]) if iv >= 0 else 15.0
                unreal = (entry - float(ask[ib])) / entry * 1e4 - TAKER_FEE_BP
                if unreal <= -stop or ts - entry_ts >= MAX_HOLD_MS:
                    rows.append((entry_ts, "stop" if unreal <= -stop else "open", unreal))
                    flat = True
                    order_px = 0.0
            ib += 1
            continue
        ts = next_tr
        price = float(px[it])
        qty = float(qt[it])
        sell_aggr = bool(maker_buy[it])
        trade_usd.append(price * qty)
        if order_px > 0 and price > 0:
            if flat and (not sell_aggr) and price >= order_px * (1 - 1e-8):
                if price > order_px * (1 + 1e-8):
                    cum = ahead + qty
                elif _same(price, order_px):
                    cum += qty
                if cum > ahead:
                    entry = order_px
                    entry_ts = ts
                    ahead_usd.append(ahead * order_px)
                    flat = False
                    order_px = 0.0
                    cum = 0.0
            elif (not flat) and sell_aggr and price <= order_px * (1 + 1e-8):
                if price < order_px * (1 - 1e-8):
                    cum = ahead + qty
                elif _same(price, order_px):
                    cum += qty
                if cum > ahead:
                    y = (entry - order_px) / entry * 1e4
                    rows.append((entry_ts, "maker", y))
                    flat = True
                    order_px = 0.0
                    cum = 0.0
        it += 1
    return rows, ahead_usd, trade_usd


def report(sym, front, max_ahead_usd=None):
    rows, ahead_usd, trade_usd = walk(sym, front, max_ahead_usd=max_ahead_usd)
    title = "排第一" if front else "排在已挂数量后面"
    if max_ahead_usd is not None:
        title += f"，卖一金额不超过 ${max_ahead_usd:.0f} 才挂"
    print(f"\n{sym}  {title}  回合 {len(rows)}")
    if trade_usd:
        arr = np.array(trade_usd)
        print(f"  单笔成交额中位 ${np.median(arr):.0f}  平均 ${arr.mean():.0f}")
    if ahead_usd:
        arr = np.array(ahead_usd)
        print(f"  进场时排在前面的金额中位 ${np.median(arr):.0f}")
    if not rows:
        return
    ys = np.array([r[2] for r in rows], dtype=float)
    kinds = [r[1] for r in rows]
    n = len(rows)
    print(f"  全部 n={n} 均{ys.mean():+.2f} 中位{np.median(ys):+.2f} "
          f"最差10%{np.quantile(ys, 0.1):+.2f} 赚到{(ys > 0).mean() * 100:.0f}%  "
          f"挂单买回{kinds.count('maker') / n * 100:.0f}% "
          f"止损{kinds.count('stop') / n * 100:.0f}%")
    times = np.array([r[0] for r in rows])
    cuts = [np.quantile(times, q) for q in (0.33, 0.66)]
    for name, mask in (
        ("前段", times <= cuts[0]),
        ("中段", (times > cuts[0]) & (times <= cuts[1])),
        ("后段", times > cuts[1]),
    ):
        part = ys[mask]
        if len(part) == 0:
            continue
        print(f"  {name} n={len(part)} 均{part.mean():+.2f} 中位{np.median(part):+.2f}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    for sym in ("PLAY", "LYN"):
        for cap in (20.0, 40.0):
            report(sym, front=False, max_ahead_usd=cap)
