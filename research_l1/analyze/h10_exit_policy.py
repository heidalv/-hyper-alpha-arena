"""H10 · 平仓腿钉价 vs 追价（H9 失败原因的定向检验）

H9 的发现（净/往返 -0.443bp，3/9 为正）：
  我的模拟里"每 5s 按新盘口重挂"让平仓腿**追价** ⇒ 完成率 98~100%，
  但持有中位从 21.8s（ASTER）到 444.5s（ZEC）⇒ mid 漂移 D(τ) 远超 H8 假设的 5s。
  **完成率高 ≠ 赚到点差**，而是"套住单边直到市场回到我这边"。

H10 的假设：
  若平仓腿**钉死在"赚到点差"的价位**（买入腿的出场 = 入场价 + 入场时点差），
  则每笔完成的往返**必然**贡献 ≈ 1×点差；代价是完成率下降、逾期单边变多。
  必须同时量出两端，才知道净额是正是负。

三种平仓政策对比（进场都是贴盘口）：
  A. chase    —— H9 原政策：每 REQUOTE_MS 按新盘口重挂（追价）
  B. pinned   —— 钉死在 入场价 ± 入场时点差（赚一个点差就跑）
  C. pinned_k —— 钉死在 入场价 ± k×入场时点差，k ∈ {1, 2}

对每种政策给：完成率、**按往返计的净收益（含未完成单边的盯市损失）**、
持有时长、以及"每次进场决策"的期望收益（这是最终口径——因为决策次数才是成本）。

关键口径（必须与 H9 一致，否则不可比）：
  总净额 = Σ(完成往返的价差) + Σ(未完成单边按期末 mid 的盯市)
  期望/进场 = 总净额 / 进场次数
"""
from __future__ import annotations

import bisect
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env", override=False)

import numpy as np  # noqa: E402
import psycopg2  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "out"
OUT.mkdir(parents=True, exist_ok=True)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
           "BNBUSDT", "ASTERUSDT", "ZECUSDT", "HYPEUSDT"]
HOURS = float(os.environ.get("H10_HOURS", "12"))
GRID_MS = 1000
REQUOTE_MS = 5000
NOTIONAL = 150.0
HOLD_OPTIONS = [30_000, 60_000, 120_000, 300_000]


def pg():
    url = os.environ["DATABASE_URL"]
    for d in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(d, "")
    head, _, _ = url.rpartition("/")
    cn = psycopg2.connect(head + "/alpha_market")
    cn.autocommit = True
    return cn


def stats(vals):
    v = np.asarray([x for x in vals if x is not None and np.isfinite(x)], dtype=np.float64)
    if v.size == 0:
        return None
    o = {"n": int(v.size), "mean": float(v.mean()), "median": float(np.median(v))}
    if v.size > 1:
        se = float(v.std(ddof=1)) / float(np.sqrt(v.size))
        o["t"] = (float(v.mean() / se) if se > 0 else None)
    return o


def simulate(sym, bts, bid, ask, bq, aq, tidx, policy, k, hold_ms):
    """policy: 'chase' | 'pinned'（pinned 用 k 倍入场点差）"""
    mid = (bid + ask) / 2.0

    def px_at(arr, ts):
        i = bisect.bisect_right(bts, ts) - 1
        return float(arr[i]) if i >= 0 else None

    def mid_at(ts):
        i = bisect.bisect_right(bts, ts) - 1
        return float(mid[i]) if i >= 0 else None

    def q_ahead_at(side, limit, ts, shown):
        ts_arr, cum_arr = tidx[side].get(round(limit, 12), (None, None))
        if ts_arr is None:
            return shown
        kk = bisect.bisect_right(ts_arr, ts)
        j = bisect.bisect_left(ts_arr, ts - 10_000)
        recent = (float(cum_arr[kk - 1]) - (float(cum_arr[j - 1]) if j > 0 else 0.0)) if kk > 0 else 0.0
        return min(shown, recent) if recent > 0 else shown

    def try_fill(side, limit, q_ahead, t_from, t_until):
        ts_arr, cum_arr = tidx[side].get(round(limit, 12), (None, None))
        if ts_arr is None:
            return None
        kk = bisect.bisect_right(ts_arr, t_from)
        if kk >= len(ts_arr):
            return None
        base = float(cum_arr[kk - 1]) if kk > 0 else 0.0
        j = bisect.bisect_right(cum_arr, base + q_ahead, lo=kk)
        if j >= len(ts_arr) or ts_arr[j] > t_until:
            return None
        return int(ts_arr[j])

    pos = 0.0
    entry_px = 0.0
    entry_ts = 0
    entry_side = None
    exit_limit = 0.0
    closed = []           # 完成往返的 bp
    open_marks = []       # 未完成单边的 bp（期末盯市）
    n_entry = 0
    n_exit = 0
    pick = 0
    t = int(bts[0])
    end = int(bts[-1])

    while t < end - 10_000:
        if pos == 0.0:
            side = "bid" if (pick % 2 == 0) else "ask"
            pick += 1
            limit = px_at(bid if side == "bid" else ask, t)
            if not limit or limit <= 0:
                t += GRID_MS
                continue
            shown = px_at(bq if side == "bid" else aq, t) or 0.0
            qa = q_ahead_at(side, limit, t, shown)
            n_entry += 1
            f = None
            seg = t
            while seg < t + hold_ms:
                se = min(seg + REQUOTE_MS, t + hold_ms)
                f = try_fill(side, limit, qa, seg, se)
                if f:
                    break
                nl = px_at(bid if side == "bid" else ask, se)
                if nl and nl > 0 and abs(nl - limit) / limit > 1e-9:
                    limit = nl
                    shown = px_at(bq if side == "bid" else aq, se) or 0.0
                    qa = q_ahead_at(side, limit, se, shown)
                seg = se
            if not f:
                t += GRID_MS
                continue
            # 建仓
            pos = (NOTIONAL / limit) * (1.0 if side == "bid" else -1.0)
            entry_px = limit
            entry_ts = f
            entry_side = side
            sp_abs = (px_at(ask, f) or limit) - (px_at(bid, f) or limit)
            if policy == "pinned":
                exit_limit = entry_px + k * sp_abs if side == "bid" else entry_px - k * sp_abs
            else:
                exit_limit = 0.0
            t = f + 1
        else:
            # 平仓腿
            side = "ask" if pos > 0 else "bid"
            if policy == "pinned":
                limit = exit_limit
            else:
                limit = px_at(ask if side == "ask" else bid, t)
                if not limit or limit <= 0:
                    t += GRID_MS
                    continue
            shown = px_at(aq if side == "ask" else bq, t) or 0.0
            qa = q_ahead_at(side, limit, t, shown)
            f = None
            seg = t
            while seg < t + hold_ms:
                se = min(seg + REQUOTE_MS, t + hold_ms)
                f = try_fill(side, limit, qa, seg, se)
                if f:
                    break
                if policy == "chase":
                    nl = px_at(ask if side == "ask" else bid, se)
                    if nl and nl > 0 and abs(nl - limit) / limit > 1e-9:
                        limit = nl
                        shown = px_at(aq if side == "ask" else bq, se) or 0.0
                        qa = q_ahead_at(side, limit, se, shown)
                seg = se
            if f:
                bp = ((limit - entry_px) if pos > 0 else (entry_px - limit)) / entry_px * 1e4
                closed.append(bp)
                n_exit += 1
                pos = 0.0
                t = f + 1
            else:
                m = mid_at(t + hold_ms)
                if m:
                    bp = ((m - entry_px) if pos > 0 else (entry_px - m)) / entry_px * 1e4
                    open_marks.append(bp)
                pos = 0.0
                t += hold_ms

    if pos != 0.0:
        m = mid_at(end - 1)
        if m:
            bp = ((m - entry_px) if pos > 0 else (entry_px - m)) / entry_px * 1e4
            open_marks.append(bp)

    total_bp = float(np.sum(closed) + np.sum(open_marks)) if (closed or open_marks) else 0.0
    return {
        "n_entry_attempts": n_entry, "n_filled_entry": len(closed) + len(open_marks),
        "n_closed": len(closed), "n_forced": len(open_marks),
        "close_rate": (len(closed) / (len(closed) + len(open_marks))) if (closed or open_marks) else None,
        "closed_bp": stats(closed), "forced_bp": stats(open_marks),
        "total_bp": total_bp,
        "bp_per_entry_attempt": (total_bp / n_entry) if n_entry else None,
        "usd_total": total_bp / 1e4 * NOTIONAL,
    }


def main():
    cn = pg()
    cur = cn.cursor()
    cur.execute("select min(event_ts_ms), max(event_ts_ms) from asterdex_book_ticker")
    lo, hi = cur.fetchone()
    cn.close()
    T1 = int(hi)
    T0 = T1 - int(HOURS * 3600 * 1000)

    data = {}
    for sym in SYMBOLS:
        cn = pg()
        cur = cn.cursor()
        cur.execute("select event_ts_ms,bid_px,bid_qty,ask_px,ask_qty from asterdex_book_ticker "
                    "where symbol=%s and event_ts_ms between %s and %s order by event_ts_ms",
                    (sym, T0, T1))
        book = cur.fetchall()
        cur.execute("select event_ts_ms,price,qty,is_buyer_maker from asterdex_trades "
                    "where symbol=%s and event_ts_ms between %s and %s order by event_ts_ms",
                    (sym, T0, T1))
        tr = cur.fetchall()
        cn.close()
        if not book or not tr:
            continue
        bts = np.array([r[0] for r in book], dtype=np.int64)
        bid = np.array([r[1] for r in book], dtype=np.float64)
        bq = np.array([r[2] for r in book], dtype=np.float64)
        ask = np.array([r[3] for r in book], dtype=np.float64)
        aq = np.array([r[4] for r in book], dtype=np.float64)
        tts = np.array([r[0] for r in tr], dtype=np.int64)
        tpx = np.array([r[1] for r in tr], dtype=np.float64)
        tqt = np.array([r[2] for r in tr], dtype=np.float64)
        bm = np.array([bool(r[3]) for r in tr])
        bh, sh = defaultdict(list), defaultdict(list)
        for i in range(len(tts)):
            (bh if bm[i] else sh)[tpx[i]].append((int(tts[i]), float(tqt[i])))
        tidx = {}
        for tag, dd in (("bid", bh), ("ask", sh)):
            built = {}
            for px, arr in dd.items():
                arr.sort()
                built[round(px, 12)] = ([a[0] for a in arr], np.cumsum([a[1] for a in arr]))
            tidx[tag] = built
        data[sym] = (bts, bid, ask, bq, aq, tidx)

    rep = {"generated_at": datetime.now(timezone.utc).isoformat(),
           "window_ms": [T0, T1], "hours": HOURS, "notional_usd": NOTIONAL,
           "policies": {}, "hold_options_ms": HOLD_OPTIONS}
    results = {}

    for hold in HOLD_OPTIONS:
        for policy, k in (("chase", 1), ("pinned", 1), ("pinned", 2)):
            key = f"{policy}_k{k}_h{hold//1000}s"
            results[key] = {}
            for sym, (bts, bid, ask, bq, aq, tidx) in data.items():
                results[key][sym] = simulate(sym, bts, bid, ask, bq, aq, tidx, policy, k, hold)

    for key, per in results.items():
        tot = sum(v["total_bp"] for v in per.values())
        usd = sum(v["usd_total"] for v in per.values())
        pe = [v["bp_per_entry_attempt"] for v in per.values() if v["bp_per_entry_attempt"] is not None]
        pos = sum(1 for x in pe if x > 0)
        rep["policies"][key] = {"total_bp": tot, "total_usd": usd,
                                "bp_per_entry_mean": float(np.mean(pe)) if pe else None,
                                "n_positive": pos, "n_symbols": len(pe),
                                "per_symbol": per}
        print(f"{key:<20} 总净 {tot:>+9.1f}bp ≈ ${usd:>+7.2f}   "
              f"每次进场 {np.mean(pe) if pe else float('nan'):>+7.3f}bp  为正 {pos}/{len(pe)}")

    # 打印细分（最佳政策 × 每标的）
    best = max(rep["policies"].items(), key=lambda kv: kv[1]["bp_per_entry_mean"] or -1e9)
    print(f"\n最佳政策 = {best[0]}")
    print(f"{'sym':<11}{'进场次':>8}{'完成':>7}{'强平':>7}{'完成率':>8}"
          f"{'完成bp':>9}{'强平bp':>9}{'合计bp':>9}{'$/笔':>9}")
    for sym, v in best[1]["per_symbol"].items():
        cb = v["closed_bp"]; fb = v["forced_bp"]
        print(f"{sym:<11}{v['n_entry_attempts']:>8}{v['n_closed']:>7}{v['n_forced']:>7}"
              f"{(v['close_rate'] or 0):>8.3f}"
              f"{(cb['mean'] if cb else float('nan')):>+9.3f}"
              f"{(fb['mean'] if fb else float('nan')):>+9.3f}"
              f"{v['total_bp']:>+9.1f}{v['usd_total']:>+9.3f}")

    p = OUT / "h10_exit_policy.json"
    p.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {p}")


if __name__ == "__main__":
    main()
