"""H16 · 挂宽扫描（在真实 tick 上，回答「挂哪里才成交、成交后赚不赚」）

## 为什么必须做这一步

现场诊断：车道 24h 只有 **2 笔成交**，而报价决策 **3964 次**（成交率 0.05%）。
根因是**挂宽 30bp**，而实际半价差只有 0.59–9.5bp ⇒ 报价挂在盘口外 **40–50 倍**，
物理上不可能成交。`meta.params` 停留在 09-17 旧口径，从未按研究结论更新。

⇒ 本脚本在**真实 tick 数据**（`asterdex_book_ticker` p50 36ms + `asterdex_trades`）
上扫挂宽，量三件事：
    ① 成交率（队列感知：累计对手方成交量 > 排我前面的量）
    ② 成交后 markout（成交时刻 mid 起算，τ 秒后）
    ③ 往返净（= 整个点差 + markout，Aster maker=0bp）

## 兜底语义（与 H10 一致，便于对照）

  进场：钉在 mid − w（挂买）/ mid + w（挂卖），队列感知成交
  出场：钉死在 入场价 ± 入场时点差（赚一个点差就跑）
  超时：HOLD 内未成交 ⇒ 按 mid 盯市强平
  指标：**每笔决策**的期望净额（决策次数才是成本）——只报"每笔往返"会掩盖低成交率

## 与 H10 的区别

H10 只测了 `pinned_k1_h30s` 等少数配置且 pinned 出场；这里专门扫**挂宽**这一维，
并同时给出「成交率」与「净额」，直接回答"改 w_base_bp 能不能让它开始成交"。
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

SYMBOLS = os.environ.get("H16_SYMBOLS", "ASTER,XRP,SOL,DOGE,UNI,SEI,VIRTUAL").split(",")
HOURS = float(os.environ.get("H16_HOURS", "6"))
GRID_MS = 1000
HOLD_MS = 30_000
WIDTHS_BP = [0.0, 0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0, 30.0]
NOTIONAL = 30.0


def conn():
    url = os.environ["DATABASE_URL"]
    for d in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(d, "")
    head, _, _ = url.rpartition("/")
    cn = psycopg2.connect(head + "/alpha_market")
    cn.autocommit = True
    return cn


def load(sym, t0, t1):
    c = conn()
    cur = c.cursor()
    cur.execute("select event_ts_ms,bid_px,bid_qty,ask_px,ask_qty from asterdex_book_ticker "
                "where symbol=%s and event_ts_ms between %s and %s order by event_ts_ms",
                (f"{sym}USDT", t0, t1))
    book = cur.fetchall()
    cur.execute("select event_ts_ms,price,qty,is_buyer_maker from asterdex_trades "
                "where symbol=%s and event_ts_ms between %s and %s order by event_ts_ms",
                (f"{sym}USDT", t0, t1))
    tr = cur.fetchall()
    c.close()
    return book, tr


def run(sym, book, tr, width_bp, hold_ms):
    bts = np.array([r[0] for r in book], dtype=np.int64)
    bid = np.array([r[1] for r in book], dtype=np.float64)
    bq = np.array([r[2] for r in book], dtype=np.float64)
    ask = np.array([r[3] for r in book], dtype=np.float64)
    aq = np.array([r[4] for r in book], dtype=np.float64)
    mid = (bid + ask) / 2.0

    tts = np.array([r[0] for r in tr], dtype=np.int64)
    tpx = np.array([r[1] for r in tr], dtype=np.float64)
    tqt = np.array([r[2] for r in tr], dtype=np.float64)
    bm = np.array([bool(r[3]) for r in tr])

    bh, sh = defaultdict(list), defaultdict(list)
    for k in range(len(tts)):
        (bh if bm[k] else sh)[tpx[k]].append((int(tts[k]), float(tqt[k])))
    tidx = {}
    for tag, dd in (("bid", bh), ("ask", sh)):
        b = {}
        for px, arr in dd.items():
            arr.sort()
            b[round(px, 12)] = ([x[0] for x in arr], np.cumsum([x[1] for x in arr]))
        tidx[tag] = b

    def mid_at(ts):
        i = bisect.bisect_right(bts, ts) - 1
        return float(mid[i]) if i >= 0 else None

    grid = np.arange(int(bts[0]), int(bts[-1]), GRID_MS, dtype=np.int64)
    gidx = np.clip(np.searchsorted(bts, grid, side="right") - 1, 0, len(bts) - 1)

    n_dec = 0
    n_fill = 0
    bps = []
    mks = []
    pick = 0
    for gi in range(len(grid)):
        t = int(grid[gi])
        m0 = float(mid[gidx[gi]])
        bb, ba = float(bid[gidx[gi]]), float(ask[gidx[gi]])
        if not (m0 > 0 and bb > 0 and ba > bb):
            continue
        sp = ba - bb
        side = "bid" if (pick % 2 == 0) else "ask"
        pick += 1
        n_dec += 1
        # 挂单价格：mid ∓ width（width_bp=0 ⇒ 恰好挂在 mid，实际会被 min_width 钳到盘口内）
        if width_bp <= 0:
            limit = bb if side == "bid" else ba          # 贴盘口
        else:
            limit = m0 * (1 - width_bp / 1e4) if side == "bid" else m0 * (1 + width_bp / 1e4)
            # 不能比盘口更优（那样是 taker）
            if side == "bid":
                limit = min(limit, bb)
            else:
                limit = max(limit, ba)
        shown = float(bq[gidx[gi]]) if side == "bid" else float(aq[gidx[gi]])
        ts_arr, cum = tidx[side].get(round(limit, 12), (None, None))
        if ts_arr is None:
            continue
        k = bisect.bisect_right(ts_arr, t)
        j0 = bisect.bisect_left(ts_arr, t - 10_000)
        recent = (float(cum[k - 1]) - (float(cum[j0 - 1]) if j0 > 0 else 0.0)) if k > 0 else 0.0
        qa = min(shown, recent) if recent > 0 else shown
        if k >= len(ts_arr):
            continue
        base = float(cum[k - 1]) if k > 0 else 0.0
        kk = bisect.bisect_right(cum, base + qa, lo=k)
        if kk >= len(ts_arr):
            continue
        tf = int(ts_arr[kk])
        n_fill += 1
        entry = limit
        # 出场：钉死在 入场价 ± 入场时点差
        exit_limit = entry + sp if side == "bid" else entry - sp
        eside = "ask" if side == "bid" else "bid"
        xs, _ = tidx[eside].get(round(exit_limit, 12), (None, None))
        completed = False
        if xs is not None:
            z = bisect.bisect_right(xs, tf)
            if z < len(xs) and xs[z] <= tf + hold_ms:
                completed = True
        if completed:
            bp = ((exit_limit - entry) if side == "bid" else (entry - exit_limit)) / entry * 1e4
        else:
            mf = mid_at(tf + hold_ms)
            if mf is None:
                continue
            bp = ((mf - entry) if side == "bid" else (entry - mf)) / entry * 1e4
        bps.append(bp)
        mks.append(((mid_at(tf + 5000) or entry) - entry) / entry * 1e4
                   * (1 if side == "bid" else -1))
    return {
        "symbol": sym, "width_bp": width_bp,
        "decisions": n_dec, "fills": n_fill,
        "fill_rate": (n_fill / n_dec) if n_dec else None,
        "net_bp_per_fill": float(np.mean(bps)) if bps else None,
        "net_bp_per_decision": (float(np.sum(bps)) / n_dec) if n_dec and bps else None,
        "mk5s_bp": float(np.mean(mks)) if mks else None,
        "usd_per_decision": (float(np.sum(bps)) / n_dec / 1e4 * NOTIONAL) if n_dec and bps else None,
    }


def main():
    c = conn()
    cur = c.cursor()
    cur.execute("select max(event_ts_ms) from asterdex_book_ticker")
    T1 = int(cur.fetchone()[0])
    T0 = T1 - int(HOURS * 3600 * 1000)
    c.close()
    print(f"窗口 {HOURS}h  {datetime.fromtimestamp(T0/1000, timezone.utc):%m-%d %H:%M} "
          f"-> {datetime.fromtimestamp(T1/1000, timezone.utc):%m-%d %H:%M} UTC")
    print(f"币 {SYMBOLS}   挂宽档 {WIDTHS_BP}bp   HOLD={HOLD_MS//1000}s")
    print()

    data = {}
    for s in SYMBOLS:
        b, t = load(s, T0, T1)
        if b and t:
            data[s] = (b, t)
            print(f"  载入 {s}: book={len(b)} trades={len(t)}")
    print()

    rep = {"generated_at": datetime.now(timezone.utc).isoformat(),
           "window": [T0, T1], "hours": HOURS, "hold_ms": HOLD_MS,
           "widths_bp": WIDTHS_BP, "notional_usd": NOTIONAL, "rows": []}

    print(f"{'width':>7}{'成交率':>9}{'成交数':>8}{'mk5s':>9}{'净/笔':>9}{'净/决策':>10}{'$/决策':>11}")
    print("-" * 64)
    for w in WIDTHS_BP:
        agg = []
        for s, (b, t) in data.items():
            agg.append(run(s, b, t, w, HOLD_MS))
        dec = sum(a["decisions"] for a in agg)
        fil = sum(a["fills"] for a in agg)
        fr = fil / dec if dec else 0.0
        per_fill = [a["net_bp_per_fill"] for a in agg if a["net_bp_per_fill"] is not None]
        per_dec = [a["net_bp_per_decision"] for a in agg if a["net_bp_per_decision"] is not None]
        mk = [a["mk5s_bp"] for a in agg if a["mk5s_bp"] is not None]
        pf = float(np.mean(per_fill)) if per_fill else float("nan")
        pd_ = float(np.mean(per_dec)) if per_dec else float("nan")
        usd = pd_ / 1e4 * NOTIONAL
        print(f"{w:>7.1f}{fr:>9.4f}{fil:>8}{float(np.mean(mk)) if mk else float('nan'):>9.2f}"
              f"{pf:>9.2f}{pd_:>10.3f}{usd:>11.5f}")
        rep["rows"].append({"width_bp": w, "decisions": dec, "fills": fil, "fill_rate": fr,
                            "mk5s_bp": float(np.mean(mk)) if mk else None,
                            "net_bp_per_fill": pf, "net_bp_per_decision": pd_,
                            "usd_per_decision": usd, "per_symbol": agg})

    p = OUT / "h16_width_sweep_live.json"
    p.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {p}")


if __name__ == "__main__":
    main()
