"""H9 · 双腿联合模拟（决定性检验：点差真的能赚到，还是会套成单边？）

H8 的算术给出上界：往返净 ≈ 整个点差 + 成交后 markout（8/9 标的为正，均值 +0.83bp）。
但它假定买入腿与卖出腿**都成交**。H7 表明成交集中在逆失衡侧 ⇒ 这个假定可疑。
H9 直接模拟「挂单进 + 挂单出」的完整库存周期，实测：
  1. 往返完成率（能不能等到反向腿成交）
  2. 完成往返的净收益（bp）
  3. 未完成往返的单边持仓损失
  4. 持仓时长分布
  5. 这一切对 HOLD 上限的敏感性

策略（论文的 balanced-inventory，落到我们的模拟器上）：
  - 空仓时：在 best bid 挂买、best ask 挂卖（各 1 个候选）
  - 有库存时：**只在平仓侧挂单**（多头 → 只挂 ask；空头 → 只挂 bid）
  - 挂单在价位上等待，超过 HOLD_MS 未成交则撤单重挂（若价格已移开）
  - 成交判定：队列感知（累计对手方成交量 > 排我前面的量）
  - 费率：Aster maker = 0bp（两侧都是挂单）

口径要点：
  - 平仓腿的限价 = 挂单时刻的 best ask/bid（**贴盘口**），不是"我的建仓价 + 点差"
  - 因此赚到的点差 = 建仓时的半价差 + 平仓时的半价差，两者都随行情浮动
    —— 这正是 H8 公式的微观实现，也是它可能失败的地方
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
HOURS = float(os.environ.get("H9_HOURS", "12"))
HOLD_MS = int(os.environ.get("H9_HOLD_MS", "300000"))   # 单腿等待上限，默认 300s
REQUOTE_MS = 5000                                        # 未成交则每 5s 按新盘口重挂
GRID_MS = 1000
NOTIONAL = 150.0                                         # 单腿名义（300 USDT 本金的一半）


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
    o = {"n": int(v.size), "mean": float(v.mean()), "median": float(np.median(v)),
         "p25": float(np.percentile(v, 25)), "p75": float(np.percentile(v, 75)),
         "share_pos": float((v > 0).mean())}
    if v.size > 1:
        sd = float(v.std(ddof=1))
        se = sd / float(np.sqrt(v.size))
        o.update({"std": sd, "t": (float(v.mean() / se) if se > 0 else None)})
    return o


def simulate(sym, bts, bid, ask, bq, aq, tidx, maker_bp, hold_ms):
    mid = (bid + ask) / 2.0
    rng = np.random.default_rng(abs(hash(sym)) % (2 ** 32))

    def px_at(arr, ts):
        i = bisect.bisect_right(bts, ts) - 1
        return float(arr[i]) if i >= 0 else None

    def mid_at(ts):
        i = bisect.bisect_right(bts, ts) - 1
        return float(mid[i]) if i >= 0 else None

    def q_ahead_at(side, limit, ts, shown):
        """排在我前面的量：同价位近 10s 对手方成交量，与 top-of-book 显示量取较小者。"""
        ts_arr, cum_arr = tidx[side].get(round(limit, 12), (None, None))
        if ts_arr is None:
            return shown
        k = bisect.bisect_right(ts_arr, ts)
        j = bisect.bisect_left(ts_arr, ts - 10_000)
        recent = (float(cum_arr[k - 1]) - (float(cum_arr[j - 1]) if j > 0 else 0.0)) if k > 0 else 0.0
        return min(shown, recent) if recent > 0 else shown

    def try_fill(side, limit, q_ahead, t_from, t_until):
        """队列感知：同价位对手方**累计**成交量超过 q_ahead 的那一刻才算成交。"""
        ts_arr, cum_arr = tidx[side].get(round(limit, 12), (None, None))
        if ts_arr is None:
            return None
        k = bisect.bisect_right(ts_arr, t_from)
        if k >= len(ts_arr):
            return None
        base = float(cum_arr[k - 1]) if k > 0 else 0.0
        kk = bisect.bisect_right(cum_arr, base + q_ahead, lo=k)
        if kk >= len(ts_arr):
            return None
        if ts_arr[kk] > t_until:
            return None
        return int(ts_arr[kk])

    pos_qty = 0.0
    entry_px = 0.0
    entry_ts = 0
    entry_side = None
    trades = []
    n_quote_events = 0
    n_entry_fills = 0
    n_exit_fills = 0
    # 确定性交替选侧，保证可复现且两侧样本均衡
    pick = 0

    t = int(bts[0])
    end = int(bts[-1])
    while t < end - 10_000:
        if pos_qty == 0.0:
            side = "bid" if (pick % 2 == 0) else "ask"
            pick += 1
        else:
            side = "ask" if pos_qty > 0 else "bid"
        limit = px_at(bid if side == "bid" else ask, t)
        if limit is None or limit <= 0:
            t += GRID_MS
            continue
        shown = px_at(bq if side == "bid" else aq, t) or 0.0
        qa = q_ahead_at(side, limit, t, shown)
        n_quote_events += 1

        filled = None
        seg_start = t
        while seg_start < t + hold_ms:
            seg_end = min(seg_start + REQUOTE_MS, t + hold_ms)
            f = try_fill(side, limit, qa, seg_start, seg_end)
            if f is not None:
                filled = (f, limit)
                break
            nl = px_at(bid if side == "bid" else ask, seg_end)
            if nl is None or nl <= 0:
                break
            if abs(nl - limit) / limit > 1e-9:
                limit = nl
                shown = px_at(bq if side == "bid" else aq, seg_end) or 0.0
                qa = q_ahead_at(side, limit, seg_end, shown)
            seg_start = seg_end

        if filled is None:
            t += GRID_MS
            continue

        fts, fpx = filled
        if pos_qty == 0.0:
            pos_qty = (NOTIONAL / fpx) * (1.0 if side == "bid" else -1.0)
            entry_px = fpx
            entry_ts = fts
            entry_side = side
            n_entry_fills += 1
            t = fts + 1
        else:
            exit_px = fpx
            if pos_qty > 0:
                gross_bp = (exit_px - entry_px) / entry_px * 1e4
            else:
                gross_bp = (entry_px - exit_px) / entry_px * 1e4
            net_bp = gross_bp - 2.0 * maker_bp
            trades.append({"bp": net_bp, "hold_ms": fts - entry_ts,
                           "entry_side": entry_side})
            n_exit_fills += 1
            pos_qty = 0.0
            t = fts + 1

    forced = None
    if pos_qty != 0.0:
        m = mid_at(end - 1)
        if m:
            if pos_qty > 0:
                gross_bp = (m - entry_px) / entry_px * 1e4
            else:
                gross_bp = (entry_px - m) / entry_px * 1e4
            forced = {"bp": gross_bp - 2.0 * maker_bp, "hold_ms": end - entry_ts,
                      "entry_side": entry_side}

    bps = [x["bp"] for x in trades]
    holds = [x["hold_ms"] for x in trades]
    by_side = {}
    for s in ("bid", "ask"):
        sub = [x["bp"] for x in trades if x["entry_side"] == s]
        by_side[s] = stats(sub)
    out = {
        "symbol": sym, "quote_events": n_quote_events,
        "entry_fills": n_entry_fills, "exit_fills": n_exit_fills,
        "completed_roundtrips": len(trades),
        "roundtrip_completion_rate": (len(trades) / n_entry_fills) if n_entry_fills else None,
        "entry_fill_rate_per_quote": (n_entry_fills / n_quote_events) if n_quote_events else None,
        "net_bp_per_roundtrip": stats(bps),
        "by_entry_side": by_side,
        "hold_ms": stats(holds),
        "total_bp": (float(np.sum(bps)) if bps else 0.0),
        "stuck_open_leg": forced,
        "est_usd_per_roundtrip": (stats(bps)["mean"] / 1e4 * NOTIONAL if bps else None),
        "est_usd_total": (float(np.sum(bps)) / 1e4 * NOTIONAL if bps else 0.0),
    }
    return out


def main():
    cn = pg()
    cur = cn.cursor()
    cur.execute("select min(event_ts_ms), max(event_ts_ms) from asterdex_book_ticker")
    lo, hi = cur.fetchone()
    cn.close()
    T1 = int(hi)
    T0 = T1 - int(HOURS * 3600 * 1000)   # 取最近 HOURS 小时（避免与 H5 完全重叠）

    fees = {}
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8000/api/trading/config/fees", timeout=20) as r:
            d = json.loads(r.read().decode("utf-8"))
        for it in d.get("items", []):
            fees[it["exchange"]] = float(it["maker_bp"])
    except Exception as e:  # noqa: BLE001
        print("warn fee:", e)
    maker_bp = fees.get("asterdex", 0.0)

    rep = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": {"hours": HOURS, "hold_ms": HOLD_MS, "requote_ms": REQUOTE_MS,
                   "notional_usd": NOTIONAL, "maker_bp": maker_bp,
                   "policy": "空仓随机选一侧挂盘口；有库存只在平仓侧挂盘口；"
                             "未成交每 requote_ms 按新盘口重挂；超 hold_ms 放弃"},
        "window_ms": [T0, T1],
        "symbols": {},
    }

    agg = []
    for sym in SYMBOLS:
        cn = pg()
        cur = cn.cursor()
        cur.execute(
            "select event_ts_ms, bid_px, bid_qty, ask_px, ask_qty from asterdex_book_ticker "
            "where symbol=%s and event_ts_ms between %s and %s order by event_ts_ms",
            (sym, T0, T1))
        book = cur.fetchall()
        cur.execute(
            "select event_ts_ms, price, qty, is_buyer_maker from asterdex_trades "
            "where symbol=%s and event_ts_ms between %s and %s order by event_ts_ms",
            (sym, T0, T1))
        trades = cur.fetchall()
        cn.close()
        if not book or not trades:
            rep["symbols"][sym] = {"error": "no data"}
            print(f"[{sym}] NO DATA")
            continue

        bts = np.array([r[0] for r in book], dtype=np.int64)
        bid = np.array([r[1] for r in book], dtype=np.float64)
        bq = np.array([r[2] for r in book], dtype=np.float64)
        ask = np.array([r[3] for r in book], dtype=np.float64)
        aq = np.array([r[4] for r in book], dtype=np.float64)

        tts = np.array([r[0] for r in trades], dtype=np.int64)
        tpx = np.array([r[1] for r in trades], dtype=np.float64)
        tqt = np.array([r[2] for r in trades], dtype=np.float64)
        bmaker = np.array([bool(r[3]) for r in trades])

        # 队列感知索引：同价位累计对手方成交量 + 该时刻前 10s 同价成交量(作 Q_ahead)
        bh, sh = defaultdict(list), defaultdict(list)
        for k in range(len(tts)):
            (bh if bmaker[k] else sh)[tpx[k]].append((int(tts[k]), float(tqt[k])))
        tidx = {}
        for tag, dd in (("bid", bh), ("ask", sh)):
            built = {}
            for px, arr in dd.items():
                arr.sort()
                built[round(px, 12)] = ([a[0] for a in arr], np.cumsum([a[1] for a in arr]))
            tidx[tag] = built

        r = simulate(sym, bts, bid, ask, bq, aq, tidx, maker_bp, HOLD_MS)
        rep["symbols"][sym] = r
        nb = r["net_bp_per_roundtrip"]
        hm = r["hold_ms"]
        print(f"[{sym:<10}] 报价{r['quote_events']:>7} 进场成交{r['entry_fills']:>5} "
              f"完成往返{r['completed_roundtrips']:>5} "
              f"完成率{(r['roundtrip_completion_rate'] or 0):.3f} "
              f"净/往返={(nb['mean'] if nb else float('nan')):+.3f}bp "
              f"(t{(nb['t'] if nb else float('nan')):+.1f}) "
              f"持有中位={(hm['median'] if hm else float('nan')):.0f}ms "
              f"合计={r['total_bp']:+.1f}bp ≈ ${r['est_usd_total']:+.2f}")
        if nb:
            agg.append(r)

    print()
    if agg:
        means = [r["net_bp_per_roundtrip"]["mean"] for r in agg]
        tot = [r["total_bp"] for r in agg]
        usd = [r["est_usd_total"] for r in agg]
        print(f"跨标的: 净/往返 均值 {np.mean(means):+.3f}bp  "
              f"为正 {sum(1 for m in means if m>0)}/{len(means)}")
        print(f"        合计 {np.sum(tot):+.1f}bp ≈ ${np.sum(usd):+.2f}"
              f"（{NOTIONAL:.0f} USD/腿, {HOURS:.0f}h, {len(agg)} 标的）")
        rep["summary"] = {"mean_net_bp": float(np.mean(means)),
                          "n_positive": int(sum(1 for m in means if m > 0)),
                          "n_symbols": len(means),
                          "total_usd": float(np.sum(usd)),
                          "hours": HOURS}

    p = OUT / "h9_two_leg_simulation.json"
    p.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {p}")


if __name__ == "__main__":
    main()
