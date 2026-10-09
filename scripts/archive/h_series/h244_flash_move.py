# -*- coding: utf-8 -*-
"""H244 真·闪崩/闪涨：**秒级**急动之后是否回摆（H243 的窗口太长了）。

# 为什么要把窗口缩到秒级

H243 用 W=60s / THR=30bp，去重叠后只有 36 个独立事件，且结论是
"急涨急跌之后超额 fwd 都是正的" ⇒ 那测的是**普通大波动**，不是**闪崩**。

"闪"的本质是**极短时间内的巨大移动**（流动性瞬间消失），
典型形态是"几秒内跳 50~200bp，随后几十秒回摆"。
窗口 60 秒会把这种事件和平滑的大波动混在一起。

⇒ 本脚本把 W 降到 **5 / 10 / 15 秒**，THR 提到 **40~150bp**，找真正的"闪"。

# 判据（延续 H243 的严格口径）

1. **去重叠**（同币相邻事件 ≥ `--min-gap` 秒）
2. **减基线**（同币同时段随机点的 fwd 分布，看**超额**）
3. **报独立事件数**，< 20 就明说不足
4. **分层**：按该小时漂移分档，看效应是否只在某一档

# 关键期望（决定了响应设计）

若真闪崩**回摆**（超额 fwd 显著为正）⇒ 响应应该是：
    **撤单 + 停加仓 + 用宽挂 maker 单慢慢出库，绝不立刻 taker 平仓**
    （taker 平仓 = 在极值点 + 最宽点差上付 4bp fee）

若真闪崩**延续** ⇒ 响应应该是：撤单 + 立刻出库（哪怕付 taker）

# 用法

    python scripts/h244_flash_move.py --hours 6
    python scripts/h244_flash_move.py --hours 6 --w 10 --thr 60
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import random
import statistics as st
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h244_flash_move.json"
CUR = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT"]
FWD = [5.0, 15.0, 30.0, 60.0, 180.0]


def market_dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    base = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        base = base.replace(j, "")
    return base.rsplit("/", 1)[0] + "/alpha_market"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--w", type=float, default=10.0, help="事件窗（秒）")
    ap.add_argument("--thr", type=float, default=60.0, help="事件阈值（bp）")
    ap.add_argument("--min-gap", type=float, default=180.0)
    a = ap.parse_args()

    import psycopg
    with psycopg.connect(market_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, event_ts_ms, (bid_px + ask_px) / 2.0
                FROM asterdex_book_ticker
                WHERE ingest_ts >= now() - (%s || ' hours')::interval
                  AND symbol = ANY(%s) AND bid_px > 0 AND ask_px > bid_px
                ORDER BY symbol, event_ts_ms ASC
            """, (str(float(a.hours) + 0.3), CUR))
            rows = cur.fetchall()
    if not rows:
        print("无数据")
        return 1

    per = {}
    for sym, ms, mid in rows:
        per.setdefault(str(sym), {})[int(ms / 1000.0)] = float(mid)
    series = {}
    for s, d in per.items():
        ks = sorted(d)
        series[s] = (ks, [d[k] for k in ks])

    print("=" * 104)
    print("H244  真·闪崩/闪涨（秒级急动）")
    print("=" * 104)
    print(f"\n  窗口 {a.hours:g}h　W={a.w:g}s　THR={a.thr:g}bp"
          f"　去重叠 ≥{a.min_gap:g}s")

    # 先看"秒级移动"的整体分布，确认阈值取得合理
    allstep = []
    for sym, (ks, px) in series.items():
        for t in range(1, len(px)):
            if px[t - 1] > 0:
                allstep.append(abs(px[t] - px[t - 1]) / px[t - 1] * 1e4)
    allstep.sort()
    if allstep:
        n = len(allstep)
        print(f"\n  逐秒 |移动| 分布（{n} 个样本）："
              f"P50 {allstep[n//2]:.3f}　P90 {allstep[int(n*.9)]:.3f}　"
              f"P99 {allstep[int(n*.99)]:.3f}　P99.9 {allstep[int(n*.999)]:.3f}　"
              f"max {allstep[-1]:.2f} bp")

    def fwd_at(ks, px, i, H):
        j = i
        n = len(ks)
        while j + 1 < n and ks[j + 1] - ks[i] < H:
            j += 1
        if j == i:
            return None
        return (px[j] - px[i]) / px[i] * 1e4

    events = []
    for sym, (ks, px) in series.items():
        n = len(ks)
        last_ev = -1e18
        j = 0
        for i in range(n):
            while j + 1 < i and ks[i] - ks[j + 1] >= a.w:
                j += 1
            if ks[i] - ks[j] < a.w:
                continue
            if px[j] <= 0:
                continue
            move = (px[i] - px[j]) / px[j] * 1e4
            if abs(move) < a.thr:
                continue
            if ks[i] - last_ev < a.min_gap:
                continue
            last_ev = ks[i]
            rec = {"sym": sym, "i": i, "ts": ks[i], "move_bp": move}
            ok = True
            for H in FWD:
                f = fwd_at(ks, px, i, H)
                if f is None:
                    ok = False
                    break
                rec[f"fwd_{int(H)}"] = f
            if ok:
                events.append(rec)

    print(f"\n  **独立事件 = {len(events)}**"
          f"（急涨 {sum(1 for e in events if e['move_bp']>0)} / "
          f"急跌 {sum(1 for e in events if e['move_bp']<0)}）")
    if events:
        mv = sorted(e["move_bp"] for e in events)
        print(f"  事件幅度 bp：min {mv[0]:+.1f}　中位 {st.median(mv):+.1f}　"
              f"max {mv[-1]:+.1f}")

    if len(events) < 20:
        print(f"\n  ⚠️ 独立事件 {len(events)} < 20 ⇒ **样本不足**")
        print(f"     这张表只能当「观测到的个案」，**不能据此设计响应**")
        print(f"     ⇒ 需拉更长窗口（`--hours 24`）或放宽阈值（`--thr` 调小）")

    # 基线
    rng = random.Random(20260922)
    bl = []
    for sym, (ks, px) in series.items():
        n = len(ks)
        for _ in range(3000):
            i = rng.randrange(0, n - 1)
            rec = {"sym": sym, "ts": ks[i]}
            ok = True
            for H in FWD:
                f = fwd_at(ks, px, i, H)
                if f is None:
                    ok = False
                    break
                rec[f"fwd_{int(H)}"] = f
            if ok:
                bl.append(rec)
    print(f"  基线随机样本 {len(bl)}")

    if events:
        print(f"\n{'━'*104}\n  一、事件 vs 基线（超额才是真信号）\n{'━'*104}")
        for lbl, sel in (("急跌", [e for e in events if e["move_bp"] < 0]),
                         ("急涨", [e for e in events if e["move_bp"] > 0])):
            if not sel:
                continue
            print(f"\n  ── {lbl}（{len(sel)} 个）")
            print(f"     {'H(s)':>6}{'事件中位':>11}{'基线中位':>11}{'超额中位':>11}"
                  f"{'事件均值':>11}{'超额均值':>11}{'为正占比':>10}")
            for H in FWD:
                k = f"fwd_{int(H)}"
                ev = [e[k] for e in sel if k in e]
                bv = [b[k] for b in bl if k in b]
                if not ev or not bv:
                    continue
                pos = sum(1 for x in ev if x > 0) / len(ev) * 100
                print(f"     {H:>6.0f}{st.median(ev):>+11.3f}{st.median(bv):>+11.3f}"
                      f"{st.median(ev)-st.median(bv):>+11.3f}{st.mean(ev):>+11.3f}"
                      f"{st.mean(ev)-st.mean(bv):>+11.3f}{pos:>9.1f}%")

        print(f"\n{'━'*104}\n  二、逐事件明细（独立事件少，可以全列）\n{'━'*104}")
        print(f"\n  {'时刻':>10}{'币':>12}{'事件bp':>9}{'fwd5':>9}{'fwd15':>9}"
              f"{'fwd30':>9}{'fwd60':>9}{'fwd180':>9}")
        for e in sorted(events, key=lambda x: x["ts"]):
            row = "".join(f"{e.get(f'fwd_{int(H)}', float('nan')):>+9.2f}" for H in FWD)
            print(f"  {dt.datetime.fromtimestamp(e['ts']):%m-%d %H:%M:%S}"
                  f"{e['sym']:>12}{e['move_bp']:>+9.1f}{row}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "hours": a.hours, "w": a.w, "thr": a.thr, "min_gap": a.min_gap,
        "n_events": len(events), "n_baseline": len(bl),
        "events": [{k: v for k, v in e.items() if k != "i"} for e in events],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
