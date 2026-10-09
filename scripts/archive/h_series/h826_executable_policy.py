# -*- coding: utf-8 -*-
"""不重叠的完整回合：卖在卖一，之后只挂买一买回；买不到就按止损吃单。

同一笔行情不再每隔 15 秒重复计数。挂买一买回的平均数只统计真正买到的，
这里改成每一笔进场都有一个结果：买到、止损、或到时间仍没买到。
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
WAIT_MS = 30_000
REQUOTE_MS = 15_000
MAX_EXIT_MS = 600_000


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


def rolling_stops(bts, bid, ask):
    """和实盘同一口径：15 秒中价，近 20 步收益率标准差的 2 倍，夹在 15 到 40bp。"""
    if len(bts) < 50:
        return np.array([], dtype=np.int64), np.array([])
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
        raw = 2.0 * (var ** 0.5)
        stops[i] = min(40.0, max(15.0, raw))
    return np.array(times, dtype=np.int64), stops


def walk(sym, stop_bp, hours=12, through=False):
    lo = int((time.time() - hours * 3600) * 1000)
    with psycopg.connect(_market_dsn(), autocommit=True) as conn:
        bts, bid, ask, tts, px = load(conn, sym, lo)
    if len(bts) < 500 or len(tts) < 200:
        print(f"\n{sym}: 数据不足")
        return []
    vol_ts, vol_stop = rolling_stops(bts, bid, ask)
    t = int(bts[0]) + 60_000
    end = int(bts[-1]) - 5_000
    j_book = 0
    k_tr = 0
    out = []
    while t < end:
        j_book = max(j_book, int(np.searchsorted(bts, t, side="right") - 1))
        if j_book < 0:
            t += REQUOTE_MS
            continue
        a0, b0 = float(ask[j_book]), float(bid[j_book])
        if a0 <= b0 or b0 <= 0:
            t += REQUOTE_MS
            continue
        spread = (a0 - b0) / ((a0 + b0) / 2.0) * 1e4
        if stop_bp is None:
            iv = int(np.searchsorted(vol_ts, t, side="right") - 1)
            stop_now = float(vol_stop[iv]) if iv >= 0 else 15.0
        else:
            stop_now = float(stop_bp)
        k_tr = max(k_tr, int(np.searchsorted(tts, t, side="right")))
        tau = None
        deadline = t + WAIT_MS
        while k_tr < len(tts) and int(tts[k_tr]) <= deadline:
            hit = float(px[k_tr]) > a0 if through else float(px[k_tr]) >= a0
            if hit:
                tau = int(tts[k_tr])
                break
            k_tr += 1
        if tau is None:
            t += REQUOTE_MS
            continue
        # 进场之后：买一随盘口移动。成交价打到当时买一算挂单买回；
        # 对手价浮亏扣 4bp 后差过止损，就吃单。
        jb = int(np.searchsorted(bts, tau, side="right") - 1)
        kt = int(np.searchsorted(tts, tau, side="right"))
        limit = tau + MAX_EXIT_MS
        kind, y, exit_ts = "open", np.nan, limit
        while True:
            next_book = int(bts[jb + 1]) if jb + 1 < len(bts) else limit + 1
            next_tr = int(tts[kt]) if kt < len(tts) else limit + 1
            ts = next_book if next_book <= next_tr else next_tr
            if ts > limit:
                jb_last = min(jb, len(bts) - 1)
                y = (a0 - float(ask[jb_last])) / a0 * 1e4 - TAKER_FEE_BP
                kind, exit_ts = "open", limit
                break
            if next_book <= next_tr:
                jb += 1
                unreal = (a0 - float(ask[jb])) / a0 * 1e4 - TAKER_FEE_BP
                if unreal <= -stop_now:
                    kind, y, exit_ts = "stop", unreal, int(bts[jb])
                    break
            else:
                swept = float(px[kt]) < float(bid[jb]) if through else float(px[kt]) <= float(bid[jb])
                if swept:
                    kind = "maker"
                    y = (a0 - float(bid[jb])) / a0 * 1e4
                    exit_ts = int(tts[kt])
                    break
                kt += 1
        out.append((t, kind, float(y), spread, exit_ts - tau, stop_now))
        t = exit_ts + 1_000
    return out


def _summ(part):
    if not part:
        return "n=0"
    ys = np.array([p[2] for p in part], dtype=float)
    kinds = [p[1] for p in part]
    n = len(part)
    maker = kinds.count("maker") / n * 100
    stop = kinds.count("stop") / n * 100
    win = (ys > 0).mean() * 100
    med = np.median(ys)
    p10 = np.quantile(ys, 0.1)
    return (f"n={n:4d} 均{ys.mean():+6.2f} 中位{med:+6.2f} "
            f"最差10%{p10:+6.2f} 赚到{win:4.0f}%  "
            f"挂单买回{maker:4.0f}% 止损{stop:4.0f}%")


def report(sym, rows, title):
    print(f"\n{sym}  {title}  不重叠回合 {len(rows)}")
    if not rows:
        return
    times = np.array([r[0] for r in rows])
    cuts = [np.quantile(times, q) for q in (0.33, 0.66)]
    folds = [
        ("全部", rows),
        ("前段", [r for r, t in zip(rows, times) if t <= cuts[0]]),
        ("中段", [r for r, t in zip(rows, times) if cuts[0] < t <= cuts[1]]),
        ("后段", [r for r, t in zip(rows, times) if t > cuts[1]]),
    ]
    for name, part in folds:
        print(f"  {name:4} {_summ(part)}")
    print("  按进场时整档价差")
    bins = [(0, 8, "窄<8"), (8, 16, "8-16"), (16, 40, "16-40"), (40, 1e9, "宽>40")]
    for lo, hi, name in bins:
        part = [r for r in rows if lo <= r[3] < hi]
        if part:
            print(f"  {name:6} {_summ(part)}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    for sym in ("PLAY", "LYN"):
        report(sym, walk(sym, stop_bp=None), "按实盘波动止损")
        report(sym, walk(sym, stop_bp=None, through=True), "实盘止损，且价格必须打穿挂单价")
