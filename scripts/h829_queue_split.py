# -*- coding: utf-8 -*-
"""新单子排在卖一（或买一）后面时，成交到底是怎么发生的。

两种成交要分开：
  同价吃掉：主动单把排在前面的数量买完，价格还停在这一档。
  打穿：价格直接越过我们的挂单价，整档都被吃掉。

买回也排在当时买一的后面，买一变了就追到新买一，重新排到队尾。
对手价浮亏扣掉 4bp 后越过止损，或者 600 秒还没买到，就吃单离场。
同一时间只持有一笔。价格相同的一瞬间，先算成交，再看盘口跳价，
避免把「刚好被吃完」误记成「别人撤单跑了」。
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
ENTRY_WAIT_MS = 180_000
MAX_HOLD_MS = 600_000


def load(conn, sym, lo_ms):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT event_ts_ms, bid_px, bid_qty, ask_px, ask_qty"
            " FROM asterdex_book_ticker"
            " WHERE symbol=%s AND event_ts_ms>%s AND bid_px>0 AND ask_px>bid_px"
            " ORDER BY event_ts_ms",
            (sym, lo_ms),
        )
        bk = cur.fetchall()
        cur.execute(
            "SELECT event_ts_ms, price, qty, is_buyer_maker FROM asterdex_trades"
            " WHERE symbol=%s AND event_ts_ms>%s ORDER BY event_ts_ms",
            (sym, lo_ms),
        )
        tr = cur.fetchall()
    if not bk or not tr:
        return None
    return (
        np.array([int(r[0]) for r in bk], dtype=np.int64),
        np.array([float(r[1]) for r in bk]),
        np.array([float(r[2]) for r in bk]),
        np.array([float(r[3]) for r in bk]),
        np.array([float(r[4]) for r in bk]),
        np.array([int(r[0]) for r in tr], dtype=np.int64),
        np.array([float(r[1]) for r in tr]),
        np.array([float(r[2]) for r in tr]),
        np.array([bool(r[3]) for r in tr]),
    )


def rolling_stops(bts, bid, ask):
    t, end = int(bts[0]), int(bts[-1])
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
    if level <= 0:
        return False
    return abs(price - level) <= level * 1e-6


def side_check(bts, bid, ask, tts, px, maker_buy):
    """主动卖应该更靠近买一。用来确认方向没有标反。"""
    hit_bid = hit_ask = 0
    step = max(1, len(tts) // 4000)
    for k in range(0, len(tts), step):
        i = int(np.searchsorted(bts, int(tts[k]), side="right") - 1)
        if i < 0:
            continue
        db = abs(float(px[k]) - float(bid[i]))
        da = abs(float(px[k]) - float(ask[i]))
        if bool(maker_buy[k]):
            hit_bid += int(db <= da)
        else:
            hit_ask += int(da <= db)
    return hit_bid, hit_ask


def simulate(pack, side):
    """side='sell' 是做空：卖在卖一，买回在买一。side='buy' 相反。"""
    bts, bid, bq, ask, aq, tts, px, qt, maker_buy = pack
    vol_ts, vol_stop = rolling_stops(bts, bid, ask)
    sell_entry = side == "sell"
    nb, nt = len(bts), len(tts)
    ib = it = 1
    flat = True
    order_px = 0.0
    ahead = 0.0
    cum = 0.0
    joined = 0
    entry = 0.0
    entry_ts = 0
    entry_kind = ""
    paper = 0.0
    ahead_usd = 0.0
    spread_bp = 0.0
    attempts = {"same": 0, "through": 0, "lift": 0, "down": 0, "timeout": 0}
    rows = []

    def stop_at(ts):
        iv = int(np.searchsorted(vol_ts, ts, side="right") - 1)
        return float(vol_stop[iv]) if 0 <= iv < len(vol_stop) else 15.0

    while ib < nb or it < nt:
        tb = int(bts[ib]) if ib < nb else 2**62
        tt = int(tts[it]) if it < nt else 2**62
        if tt <= tb:
            ts = tt
            price, qty = float(px[it]), float(qt[it])
            aggr_sell = bool(maker_buy[it])
            it += 1
            if order_px <= 0 or qty <= 0 or price <= 0:
                continue
            want_sell_aggr = (not sell_entry) if flat else sell_entry
            if aggr_sell != want_sell_aggr:
                continue
            if flat:
                # 打穿的成交量也要先超过排在前面的数量。
                # 只印在更差的价格上、但数量不够吃掉队列的，不算我们成交。
                beyond = (sell_entry and price > order_px * (1 + 1e-6)) or (
                    (not sell_entry) and price < order_px * (1 - 1e-6))
                if beyond or _same(price, order_px):
                    cum += qty
                    if cum <= ahead:
                        continue
                    through = bool(beyond)
                else:
                    continue
                entry = order_px
                entry_ts = ts
                entry_kind = "through" if through else "same"
                attempts[entry_kind] += 1
                j = int(np.searchsorted(bts, ts, side="right") - 1)
                if j >= 0:
                    mid = (float(bid[j]) + float(ask[j])) / 2.0
                    paper = (entry - mid) / entry * 1e4 if sell_entry else (mid - entry) / entry * 1e4
                    spread_bp = (float(ask[j]) - float(bid[j])) / mid * 1e4
                flat = False
                order_px = 0.0
                cum = 0.0
                ahead = 0.0
            else:
                beyond = (sell_entry and price < order_px * (1 - 1e-6)) or (
                    (not sell_entry) and price > order_px * (1 + 1e-6))
                if beyond or _same(price, order_px):
                    cum += qty
                    if cum <= ahead:
                        continue
                    fill_px = order_px
                else:
                    continue
                y = (entry - fill_px) / entry * 1e4 if sell_entry else (fill_px - entry) / entry * 1e4
                rows.append((entry_ts, entry_kind, y, paper, ahead_usd, spread_bp, "maker"))
                flat = True
                order_px = 0.0
                cum = 0.0
            continue
        ts = tb
        ib += 1
        j = ib - 1
        level = float(ask[j]) if (flat and sell_entry) or ((not flat) and not sell_entry) else float(bid[j])
        qty = float(aq[j]) if (flat and sell_entry) or ((not flat) and not sell_entry) else float(bq[j])
        if flat:
            if order_px <= 0:
                order_px, ahead, cum, joined = level, max(qty, 0.0), 0.0, ts
                ahead_usd = ahead * level
                continue
            if ts - joined >= ENTRY_WAIT_MS:
                attempts["timeout"] += 1
                order_px, ahead, cum, joined = level, max(qty, 0.0), 0.0, ts
                ahead_usd = ahead * level
                continue
            if not _same(level, order_px):
                up = level > order_px
                bad = up if sell_entry else not up
                attempts["lift" if bad else "down"] += 1
                order_px, ahead, cum, joined = level, max(qty, 0.0), 0.0, ts
                ahead_usd = ahead * level
            continue
        if order_px <= 0 or not _same(level, order_px):
            order_px, ahead, cum = level, max(qty, 0.0), 0.0
        cover = float(ask[j]) if sell_entry else float(bid[j])
        if sell_entry:
            unreal = (entry - cover) / entry * 1e4 - TAKER_FEE_BP
        else:
            unreal = (cover - entry) / entry * 1e4 - TAKER_FEE_BP
        if unreal <= -stop_at(ts) or ts - entry_ts >= MAX_HOLD_MS:
            why = "stop" if unreal <= -stop_at(ts) else "open"
            rows.append((entry_ts, entry_kind, unreal, paper, ahead_usd, spread_bp, why))
            flat = True
            order_px, ahead, cum = 0.0, 0.0, 0.0
    return attempts, rows


def _line(name, rows):
    if not rows:
        print(f"  {name:8} n=0")
        return
    ys = np.array([r[2] for r in rows], dtype=float)
    papers = np.array([r[3] for r in rows], dtype=float)
    kinds = [r[6] for r in rows]
    n = len(rows)
    print(f"  {name:8} n={n:4d} 刚卖出账面上{papers.mean():+6.2f}  "
          f"买回后{ys.mean():+6.2f} 中位{np.median(ys):+6.2f} "
          f"最差10%{np.quantile(ys, 0.1):+6.2f} 赚到{(ys > 0).mean() * 100:4.0f}%  "
          f"挂单买回{kinds.count('maker') / n * 100:4.0f}% 止损{kinds.count('stop') / n * 100:4.0f}%")


def report(sym, side, attempts, rows):
    label = "做空" if side == "sell" else "做多"
    n_try = sum(attempts.values())
    print(f"\n{sym} {label}  尝试 {n_try}  "
          f"同价成交 {attempts['same']}  打穿 {attempts['through']}  "
          f"价格跑了撤单 {attempts['lift']}  价格反向撤单 {attempts['down']}  "
          f"超时 {attempts['timeout']}")
    _line("全部成交", rows)
    _line("同价吃掉", [r for r in rows if r[1] == "same"])
    _line("打穿成交", [r for r in rows if r[1] == "through"])
    if len(rows) < 9:
        return
    times = np.array([r[0] for r in rows])
    cuts = [np.quantile(times, q) for q in (0.33, 0.66)]
    folds = (
        ("前段", rows and [r for r in rows if r[0] <= cuts[0]]),
        ("中段", [r for r in rows if cuts[0] < r[0] <= cuts[1]]),
        ("后段", [r for r in rows if r[0] > cuts[1]]),
    )
    for name, part in folds:
        _line(name, part)
    print("  按进场时排在前面的金额")
    for lo, hi, name in ((0, 30, "<30"), (30, 100, "30-100"),
                         (100, 300, "100-300"), (300, 1e12, ">300")):
        _line(name, [r for r in rows if lo <= r[4] < hi])


def symbols(conn, lo_ms):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol, count(*) FROM asterdex_book_ticker"
            " WHERE event_ts_ms>%s GROUP BY symbol ORDER BY 2 DESC LIMIT 8",
            (lo_ms,),
        )
        return [r[0] for r in cur.fetchall()]


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    lo = int((time.time() - 12 * 3600) * 1000)
    with psycopg.connect(_market_dsn(), autocommit=True) as conn:
        names = ["PLAYUSDT", "LYNUSDT", "BTCUSDT", "ETHUSDT", "QNTUSDT", "BTWUSDT"]
        print("本轮合约:", ", ".join(names))
        for name in names:
            pack = load(conn, name, lo)
            if pack is None or len(pack[0]) < 500 or len(pack[5]) < 200:
                print(f"\n{name}: 数据不足")
                continue
            bts, bid, _bq, ask, _aq, tts, px, _qt, maker_buy = pack
            hb, ha = side_check(bts, bid, ask, tts, px, maker_buy)
            print(f"\n{name} 方向核对 主动卖靠近买一 {hb}  主动买靠近卖一 {ha}")
            for side in ("sell", "buy"):
                report(name, side, *simulate(pack, side))
