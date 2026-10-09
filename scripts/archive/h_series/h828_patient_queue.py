# -*- coding: utf-8 -*-
"""不跟着卖一改价。挂上之后留在原价，等排在前面的量被吃掉或被撤掉。

卖一抬高、原价上的量消失，就变成这一档的第一位。
卖一跌破我们的价格，说明市场已经走了，撤单再挂。
超过 60 秒还没卖出，也撤单再挂。
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
ENTRY_WAIT_MS = 60_000


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
    return (
        np.array([r[0] for r in bk], dtype=np.int64),
        np.array([float(r[1]) for r in bk]),
        np.array([float(r[2]) for r in bk]),
        np.array([float(r[3]) for r in bk]),
        np.array([float(r[4]) for r in bk]),
        np.array([r[0] for r in tr], dtype=np.int64),
        np.array([float(r[1]) for r in tr]),
        np.array([float(r[2]) for r in tr]),
        np.array([bool(r[3]) for r in tr]),
    )


def stops_of(bts, bid, ask):
    t, end = int(bts[0]), int(bts[-1])
    times, mids = [], []
    while t <= end:
        i = int(np.searchsorted(bts, t, side="right") - 1)
        if i >= 0:
            mids.append((float(bid[i]) + float(ask[i])) / 2.0)
            times.append(t)
        t += 15_000
    out = np.full(len(times), 15.0)
    for i in range(21, len(mids)):
        xs = mids[i - 20:i + 1]
        rets = [(xs[k] - xs[k - 1]) / xs[k - 1] * 1e4
                for k in range(1, len(xs)) if xs[k - 1] > 0]
        if len(rets) < 2:
            continue
        mu = sum(rets) / len(rets)
        var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
        out[i] = min(40.0, max(15.0, 2.0 * (var ** 0.5)))
    return np.array(times, dtype=np.int64), out


def _same(price, level):
    return abs(price - level) <= level * 1e-8


def walk(sym, hours=12):
    lo = int((time.time() - hours * 3600) * 1000)
    with psycopg.connect(_market_dsn(), autocommit=True) as conn:
        bts, bid, bq, ask, aq, tts, px, qt, maker_buy = load(conn, sym, lo)
    if len(bts) < 500 or len(tts) < 200:
        print(f"\n{sym}: 数据不足")
        return []
    vol_ts, vol_stop = stops_of(bts, bid, ask)
    ib, it = 1, int(np.searchsorted(tts, int(bts[0]), side="right"))
    flat = True
    order_px = 0.0
    ahead = 0.0
    cum = 0.0
    joined = 0
    entry = 0.0
    entry_ts = 0
    became_first = 0
    rows = []
    while ib < len(bts) and it < len(tts):
        book_first = int(bts[ib]) <= int(tts[it])
        ts = int(bts[ib]) if book_first else int(tts[it])
        if book_first:
            if flat:
                level, qty = float(ask[ib]), float(aq[ib])
                if order_px <= 0 or ts - joined >= ENTRY_WAIT_MS:
                    order_px, ahead, cum, joined = level, max(qty, 0.0), 0.0, ts
                elif level > order_px * (1 + 1e-8):
                    if ahead > 0:
                        became_first += 1
                    ahead = 0.0
                elif level < order_px * (1 - 1e-8):
                    order_px, ahead, cum, joined = level, max(qty, 0.0), 0.0, ts
            else:
                level = float(bid[ib])
                if order_px <= 0 or not _same(level, order_px):
                    order_px, ahead, cum = level, max(float(bq[ib]), 0.0), 0.0
                iv = int(np.searchsorted(vol_ts, ts, side="right") - 1)
                stop = float(vol_stop[iv]) if iv >= 0 else 15.0
                unreal = (entry - float(ask[ib])) / entry * 1e4 - TAKER_FEE_BP
                if unreal <= -stop or ts - entry_ts >= MAX_HOLD_MS:
                    rows.append((entry_ts, "stop" if unreal <= -stop else "open", unreal))
                    flat, order_px = True, 0.0
            ib += 1
            continue
        price, qty = float(px[it]), float(qt[it])
        sell_aggr = bool(maker_buy[it])
        if order_px > 0:
            if flat and (not sell_aggr) and price >= order_px * (1 - 1e-8):
                if price > order_px * (1 + 1e-8) or _same(price, order_px):
                    cum = ahead + qty if price > order_px * (1 + 1e-8) else cum + qty
                if cum > ahead:
                    entry, entry_ts = order_px, ts
                    flat, order_px, cum = False, 0.0, 0.0
            elif (not flat) and sell_aggr and price <= order_px * (1 + 1e-8):
                if price < order_px * (1 - 1e-8) or _same(price, order_px):
                    cum = ahead + qty if price < order_px * (1 - 1e-8) else cum + qty
                if cum > ahead:
                    rows.append((entry_ts, "maker", (entry - order_px) / entry * 1e4))
                    flat, order_px, cum = True, 0.0, 0.0
        it += 1
    print(f"  原价变成第一位的次数 {became_first}")
    return rows


def report(sym):
    print(f"\n{sym}  不改价，等队列")
    rows = walk(sym)
    if not rows:
        print("  没有成交")
        return
    ys = np.array([r[2] for r in rows], dtype=float)
    kinds = [r[1] for r in rows]
    n = len(ys)
    print(f"  n={n} 均{ys.mean():+.2f} 中位{np.median(ys):+.2f} "
          f"最差10%{np.quantile(ys, 0.1):+.2f} 赚到{(ys > 0).mean() * 100:.0f}%  "
          f"挂单买回{kinds.count('maker') / n * 100:.0f}% 止损{kinds.count('stop') / n * 100:.0f}%")
    times = np.array([r[0] for r in rows])
    cuts = [np.quantile(times, q) for q in (0.33, 0.66)]
    for name, mask in (
        ("前段", times <= cuts[0]),
        ("中段", (times > cuts[0]) & (times <= cuts[1])),
        ("后段", times > cuts[1]),
    ):
        part = ys[mask]
        if len(part):
            print(f"  {name} n={len(part)} 均{part.mean():+.2f} 中位{np.median(part):+.2f}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    for sym in ("PLAY", "LYN", "BTC"):
        report(sym)
