# -*- coding: utf-8 -*-
"""H243 突发事件实测（修正 H242 的三个统计硬伤）。

# H242 错在哪

H242 报"6 小时检出 9,522 个事件（急涨 7,801 / 急跌 1,721）"，
并据此说"急跌后回摆 70.4%、急涨后继续跌 53.5%"。**三个硬伤**：

**① 样本高度重叠（最严重）**
事件窗 W=60s、采样 1 tick/s ⇒ 同一段 5 分钟走势会被计数**几十次**。
9,522 个"事件"实际上可能只有**几十个独立的行情段**。
⇒ n 被虚增两个数量级，任何"占比/中位数"的置信度都是假的。

**② 没有漂移基线**
`fwd_60 中位 = −2.73bp`（急涨后）到底是"回摆"还是"这段时间本来就在跌"？
必须与**同期随机点的 fwd 分布**比较。不比较就等于把趋势当成回摆。

**③ 方向失衡本身是信号**
急涨 7,801 vs 急跌 1,721（4.5:1）⇒ 该窗口是**单边上涨**行情。
在这种窗口里测出来的"急跌后回摆"很可能是**趋势的产物**，不可外推。

# 本脚本的三个修正

1. **去重叠**：相邻事件至少间隔 `--min-gap` 秒（默认 300 = 5 分钟）。
   一个"独立事件"= 该币在该时刻进入急动状态；随后 5 分钟内不再重复计数。
2. **减基线**：对同一 (币, 小时) 抽**随机时刻**算 fwd 分布，
   事件组的 fwd **减去**同时段随机组的 fwd 中位 ⇒ **超额**才是真信号。
3. **分层报方向**：把窗口按"该小时净漂移"分成上行/下行/横盘三档，
   看事件效应是否**只在某一档出现**（跨窗口判据的加强版）。

# 判据

· 若「超额 fwd」在**三档都同号** ⇒ 事件效应真实（可据它设计响应）
· 若只在某一档出现 ⇒ 那是趋势的产物，**不可外推**
· n 必须报**去重叠后的独立事件数**，且若 < 30 就明说"不足"

# 用法

    python scripts/h243_sudden_move_clean.py --hours 6 --w 60 --thr 30
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
OUT = ROOT / "research_l1" / "out" / "h243_sudden_move_clean.json"
CUR = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT"]
FWD = [15.0, 30.0, 60.0, 120.0, 300.0]


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


def q(v, p):
    if not v:
        return 0.0
    s = sorted(v)
    return s[min(len(s) - 1, max(0, int(round(p / 100.0 * (len(s) - 1)))))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--w", type=float, default=60.0)
    ap.add_argument("--thr", type=float, default=30.0)
    ap.add_argument("--step", type=float, default=8.0)
    ap.add_argument("--min-gap", type=float, default=300.0,
                    help="去重叠：同币相邻事件的最小间隔（秒）")
    ap.add_argument("--grid", type=float, default=1.0, help="重采样网格（秒）")
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

    # 1s 网格取末值
    per = {}
    for sym, ms, mid in rows:
        per.setdefault(str(sym), {})[int(ms / 1000.0 / a.grid)] = float(mid)
    series = {}
    for s, d in per.items():
        ks = sorted(d)
        series[s] = (ks, [d[k] for k in ks])

    print("=" * 104)
    print("H243  突发事件实测（去重叠 + 减基线）")
    print("=" * 104)
    print(f"\n  窗口 {a.hours:g}h　W={a.w:g}s　THR={a.thr:g}bp　STEP≥{a.step:g}bp"
          f"　去重叠间隔 ≥{a.min_gap:g}s")
    for s, (ks, px) in sorted(series.items()):
        print(f"  {s:12} {len(ks):>7} 点　"
              f"{dt.datetime.fromtimestamp(ks[0]):%H:%M:%S} ~ "
              f"{dt.datetime.fromtimestamp(ks[-1]):%H:%M:%S}")

    def fwd_at(ks, px, i, H):
        j = i
        n = len(ks)
        while j + 1 < n and ks[j + 1] - ks[i] < H:
            j += 1
        if j == i:
            return None
        return (px[j] - px[i]) / px[i] * 1e4

    # ── 事件检出（带去重叠）──
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
            step_max = 0.0
            for t in range(j + 1, i + 1):
                if px[t - 1] > 0:
                    step_max = max(step_max, abs(px[t] - px[t - 1]) / px[t - 1] * 1e4)
            if step_max < a.step:
                continue
            if ks[i] - last_ev < a.min_gap:
                continue
            last_ev = ks[i]
            rec = {"sym": sym, "i": i, "ts": ks[i], "move_bp": move}
            any_ok = False
            for H in FWD:
                f = fwd_at(ks, px, i, H)
                if f is None:
                    break
                rec[f"fwd_{int(H)}"] = f
                any_ok = True
            if any_ok:
                events.append(rec)

    print(f"\n  **去重叠后的独立事件 = {len(events)}**"
          f"（急涨 {sum(1 for e in events if e['move_bp']>0)} / "
          f"急跌 {sum(1 for e in events if e['move_bp']<0)}）")
    print(f"  对照 H242 未去重叠时的计数：9522"
          f" ⇒ 虚增 **{9522/max(1,len(events)):.0f} 倍**")
    if len(events) < 30:
        print(f"\n  ⚠️ 独立事件 < 30 ⇒ **样本不足，以下所有数字仅供参考，不可据此改参数**")

    # ── 基线：随机时刻的 fwd（同币、同时段）──
    rng = random.Random(20260922)
    base_samples = []
    for sym, (ks, px) in series.items():
        n = len(ks)
        if n < 100:
            continue
        for _ in range(4000):
            i = rng.randrange(0, n - 1)
            rec = {"sym": sym, "i": i, "ts": ks[i], "move_bp": 0.0}
            ok = True
            for H in FWD:
                f = fwd_at(ks, px, i, H)
                if f is None:
                    ok = False
                    break
                rec[f"fwd_{int(H)}"] = f
            if ok:
                base_samples.append(rec)
    print(f"\n  基线随机样本 {len(base_samples)}（同币同时段）")

    # ── 一、事件 vs 基线（超额）──
    print(f"\n{'━'*104}\n  一、事件组 vs 随机基线：**超额** fwd 才是真信号\n{'━'*104}")
    for lbl, sel in (("急跌", [e for e in events if e["move_bp"] < 0]),
                     ("急涨", [e for e in events if e["move_bp"] > 0])):
        if not sel:
            continue
        print(f"\n  ── {lbl}（{len(sel)} 个独立事件）")
        print(f"     {'H(s)':>7}{'事件fwd中位':>13}{'基线fwd中位':>13}"
              f"{'超额中位':>11}{'事件均值':>11}{'基线均值':>11}{'超额均值':>11}")
        for H in FWD:
            k = f"fwd_{int(H)}"
            ev = [e[k] for e in sel if k in e]
            bs = [b[k] for b in base_samples if k in b]
            if not ev or not bs:
                continue
            print(f"     {H:>7.0f}{st.median(ev):>+13.3f}{st.median(bs):>+13.3f}"
                  f"{st.median(ev)-st.median(bs):>+11.3f}"
                  f"{st.mean(ev):>+11.3f}{st.mean(bs):>+11.3f}"
                  f"{st.mean(ev)-st.mean(bs):>+11.3f}")

    # ── 二、按漂移分层 ──
    print(f"\n{'━'*104}\n  二、按「该小时净漂移」分层（检验事件效应是否只是趋势的产物）\n{'━'*104}")
    def hour_of(ts):
        return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:00")
    # 逐 (币,小时) 的净漂移
    drift = {}
    for sym, (ks, px) in series.items():
        for h in set(hour_of(t) for t in ks):
            idx = [i for i, t in enumerate(ks) if hour_of(t) == h]
            if len(idx) < 30:
                continue
            i0, i1 = idx[0], idx[-1]
            if px[i0] > 0:
                drift[(sym, h)] = (px[i1] - px[i0]) / px[i0] * 1e4
    if drift:
        dvals = sorted(drift.values())
        lo_t = dvals[len(dvals) // 3]
        hi_t = dvals[2 * len(dvals) // 3]
        print(f"\n  逐(币,小时)漂移分位：下三分位 {lo_t:+.1f}bp　上三分位 {hi_t:+.1f}bp")
        for band, cond in (("下行档", lambda d: d <= lo_t),
                           ("横盘档", lambda d: lo_t < d < hi_t),
                           ("上行档", lambda d: d >= hi_t)):
            sel = [e for e in events
                   if (e["sym"], hour_of(e["ts"])) in drift
                   and cond(drift[(e["sym"], hour_of(e["ts"]))])]
            bs = [b for b in base_samples
                  if (b["sym"], hour_of(b["ts"])) in drift
                  and cond(drift[(b["sym"], hour_of(b["ts"]))])]
            if len(sel) < 5 or len(bs) < 20:
                print(f"\n  {band}：事件 {len(sel)} / 基线 {len(bs)} ⇒ 样本不足")
                continue
            k = "fwd_60"
            ev = [e[k] for e in sel if k in e]
            bv = [b[k] for b in bs if k in b]
            if not ev or not bv:
                continue
            print(f"\n  {band}：事件 {len(ev)} / 基线 {len(bv)}"
                  f"　事件中位 {st.median(ev):+.3f}　基线中位 {st.median(bv):+.3f}"
                  f"　**超额 {st.median(ev)-st.median(bv):+.3f} bp**")
    else:
        print("\n  逐小时漂移算不出（样本不足）")

    # ── 三、逐小时（去重叠后）──
    print(f"\n{'━'*104}\n  三、逐小时（去重叠后）\n{'━'*104}")
    hrs = {}
    for e in events:
        hrs.setdefault(hour_of(e["ts"]), []).append(e)
    print(f"\n  {'小时':<15}{'事件数':>8}{'急跌数':>8}{'急跌fwd60中位':>15}"
          f"{'急涨fwd60中位':>15}")
    for h in sorted(hrs):
        v = hrs[h]
        dn = [e["fwd_60"] for e in v if e["move_bp"] < 0 and "fwd_60" in e]
        up = [e["fwd_60"] for e in v if e["move_bp"] > 0 and "fwd_60" in e]
        print(f"  {h:<15}{len(v):>8}{len(dn):>8}"
              f"{(st.median(dn) if dn else 0):>+15.3f}"
              f"{(st.median(up) if up else 0):>+15.3f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "hours": a.hours, "w": a.w, "thr": a.thr, "min_gap": a.min_gap,
        "n_events_dedup": len(events),
        "n_events_raw_h242": 9522,
        "n_baseline": len(base_samples),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
