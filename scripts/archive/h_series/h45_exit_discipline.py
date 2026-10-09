"""H45：出场价纪律 —— 不向下追价，这是左尾的真正来源。

## H44 为什么无效（诊断清楚了）

H44 用信号过滤"入场时刻挂不挂单"，结果**三种模式的中位几乎完全相同**（+0.7245）：

    F0 不过滤      均值 −0.4005bp   中位 +0.7245   p5 −8.132
    F1 抑制逆风侧   均值 −0.4051bp   中位 +0.7245   p5 −8.009
    F2 双阈值暂停   均值 −0.3882bp   中位 +0.7246   p5 −8.149

**⇒ 左尾不来自"入场决定"，而来自出场路径。**

## 真正的原因：**出场腿向下追价**

H43/H44 的出场规则是「每 15s 按**当前**最优卖价重挂」。
价格一跌，我们就把卖单挂得更低 ⇒ **等于主动投降**，亏损在那里兑现。

分币 p5（最差 5% 的往返）：

    BTC   −1.359bp   ← 亏损小
    ETH   −2.611bp
    SOL   −8.132bp
    XRP   −8.394bp
    ASTER −10.530bp  ← 少数往返亏 10bp

若出场价**不向下移动**（只接受 ≥ 目标价），这些大亏就**不会发生** ——
代价是**卡住的往返变多**（那些最终要走 taker 强平）。

## 三种出场纪律（对照）

    **E0 向下追价**（H43/H44 的现状）：每 rq 用当前最优卖价重挂
    **E1 不向下追**：出场目标 = 入场时的最优卖价 × (1 + w/1e4)，**固定不动**；
        超时 ⇒ taker 强平
    **E2 半程后追价**：前 rq×N 秒不追（给市场回来的机会），之后才允许追价

## 判据（事先定死）

  · 均值转正 且 强平 < 30% ⇒ **出场纪律就是解**
  · 均值改善但强平 > 30% ⇒ 方向对，需调 max_hold / 追价起点
  · 均值无改善 ⇒ 亏损不来自追价，另找

用法：
    .venv\\Scripts\\python.exe scripts\\h45_exit_discipline.py --hours 24
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

OUT = ROOT / "research_l1" / "out" / "h45_exit_discipline.json"
MODES = ("E0 向下追价", "E1 不向下追", "E2 半程后追价")


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def run_symbol(s, hours, w_bp, quote_every_s, max_hold_s, requote_s,
               maker_fee_bp, taker_fee_bp, half_life_s):
    import numpy as np
    import psycopg2
    import psycopg2.extras

    vs = s if s.endswith("USDT") else f"{s}USDT"
    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    since = f"(extract(epoch from now())*1000)::bigint - {int(hours*3600_000)}"
    cur.execute(
        "SELECT event_ts_ms, (bids->0->>0)::float bp"
        f"  FROM asterdex_depth_snapshots WHERE symbol=%s AND event_ts_ms > {since}"
        " ORDER BY event_ts_ms", (vs,))
    dep = cur.fetchall()
    cur.execute(
        "SELECT event_ts_ms, price::float p, is_buyer_maker ibm"
        f"  FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > {since}"
        " ORDER BY event_ts_ms", (vs,))
    bt = cur.fetchall()
    cur.execute(
        "SELECT event_ts_ms, bid_px::float b, ask_px::float a"
        f"  FROM asterdex_book_ticker WHERE symbol=%s AND event_ts_ms > {since}"
        " ORDER BY event_ts_ms", (vs,))
    tk = cur.fetchall()
    cn.close()
    if len(dep) < 2000 or len(bt) < 1000 or len(tk) < 2000:
        return None

    dts = np.array([int(x["event_ts_ms"]) for x in dep], dtype=np.int64)
    dbp = np.array([float(x["bp"] or 0) for x in dep])
    tts = np.array([int(x["event_ts_ms"]) for x in bt], dtype=np.int64)
    tpx = np.array([float(x["p"]) for x in bt])
    tsell = np.array([bool(x["ibm"]) for x in bt])
    kts = np.array([int(x["event_ts_ms"]) for x in tk], dtype=np.int64)
    kask = np.array([float(x["a"] or 0) for x in tk])
    kbid = np.array([float(x["b"] or 0) for x in tk])
    kmid = (kbid + kask) / 2.0

    def ask_at(t_ms):
        i = int(np.searchsorted(kts, t_ms, "left"))
        return float(kask[i]) if i < len(kts) else 0.0

    def mid_at(t_ms):
        i = int(np.searchsorted(kts, t_ms, "left"))
        return float(kmid[i]) if i < len(kts) else 0.0

    step = max(1, int(quote_every_s * 1000 / max(1, int(np.median(np.diff(dts)) or 2000))))
    qidx = np.arange(0, len(dts), step, dtype=np.int64)
    max_ms = int(max_hold_s * 1000)
    rq = max(1000, int(requote_s * 1000))
    half_ms = int(half_life_s * 1000)

    res = {m: {"pnl": [], "holds": [], "forced": 0} for m in MODES}
    for i in qidx:
        bid = float(dbp[i])
        if bid <= 0:
            continue
        px_buy = bid * (1.0 - w_bp / 1e4)
        t0 = int(dts[i])
        j0 = int(np.searchsorted(tts, t0, "left"))
        j1 = int(np.searchsorted(tts, t0 + max_ms, "left"))
        je = -1
        for jj in range(j0, min(j1, len(tts))):
            if tsell[jj] and tpx[jj] <= px_buy * (1.0 + 1.0 / 1e4):
                je = jj
                break
        if je < 0:
            continue
        t_entry = int(tts[je])
        entry_px = float(tpx[je])
        a_entry = ask_at(t_entry)
        if a_entry <= 0:
            continue
        deadline = t_entry + max_ms
        target_fixed = a_entry * (1.0 + w_bp / 1e4)     # E1 的固定目标
        t_switch = t_entry + half_ms                     # E2 的追价起点

        for m in MODES:
            exit_px = None
            t_exit = None
            t_cur = t_entry
            while t_cur < deadline:
                if m == "E1 不向下追":
                    px_sell = target_fixed
                elif m == "E2 半程后追价":
                    # 半程前用固定目标；之后允许用当前 ask
                    px_sell = target_fixed if t_cur < t_switch else max(
                        target_fixed, ask_at(t_cur) * (1.0 + w_bp / 1e4))
                else:
                    a0 = ask_at(t_cur)
                    if a0 <= 0:
                        t_cur += rq
                        continue
                    px_sell = a0 * (1.0 + w_bp / 1e4)
                t_next = min(t_cur + rq, deadline)
                j2 = int(np.searchsorted(tts, t_cur, "left"))
                j3 = int(np.searchsorted(tts, t_next, "left"))
                for jj in range(j2, min(j3, len(tts))):
                    if (not tsell[jj]) and tpx[jj] >= px_sell * (1.0 - 1.0 / 1e4):
                        exit_px = float(tpx[jj])
                        t_exit = int(tts[jj])
                        break
                if exit_px is not None:
                    break
                t_cur = t_next
            if exit_px is None:
                mm = mid_at(deadline)
                if mm <= 0:
                    continue
                net = (mm * (1.0 - taker_fee_bp / 1e4) - entry_px) / entry_px * 1e4 - maker_fee_bp
                res[m]["pnl"].append(net)
                res[m]["holds"].append(deadline - t_entry)
                res[m]["forced"] += 1
            else:
                net = (exit_px - entry_px) / entry_px * 1e4 - 2 * maker_fee_bp
                res[m]["pnl"].append(net)
                res[m]["holds"].append(t_exit - t_entry)

    out = {"symbol": s, "by_mode": {}}
    for m in MODES:
        d = res[m]
        if not d["pnl"]:
            out["by_mode"][m] = None
            continue
        p = np.array(d["pnl"])
        out["by_mode"][m] = {
            "n": len(p), "n_forced": d["forced"],
            "net_bp_mean": float(p.mean()), "net_bp_median": float(np.median(p)),
            "p5": float(np.percentile(p, 5)), "p25": float(np.percentile(p, 25)),
            "p95": float(np.percentile(p, 95)),
            "win_rate": float((p > 0).mean()),
            "hold_ms_median": float(np.median(d["holds"])),
            "forced_rate": d["forced"] / max(1, len(p)),
        }
    return out


def main() -> int:
    import numpy as np

    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--symbols", default="BTC,ETH,SOL,XRP,ASTER")
    ap.add_argument("--width-bp", type=float, default=0.5)
    ap.add_argument("--quote-every-s", type=float, default=15.0)
    ap.add_argument("--requote-s", type=float, default=15.0)
    ap.add_argument("--max-hold-s", type=float, default=300.0)
    ap.add_argument("--half-life-s", type=float, default=60.0,
                    help="E2 模式下多久后才允许向下追价")
    ap.add_argument("--maker-fee-bp", type=float, default=0.0)
    ap.add_argument("--taker-fee-bp", type=float, default=4.0)
    args = ap.parse_args()
    syms = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]

    print("H45 出场纪律对照（不向下追价 vs 追价）")
    print(f"窗口={args.hours}h  币={len(syms)}  挂宽={args.width_bp}bp  "
          f"最长持有={args.max_hold_s:.0f}s  E2 追价起点={args.half_life_s:.0f}s\n")

    per = []
    for s in syms:
        r = run_symbol(s, args.hours, args.width_bp, args.quote_every_s, args.max_hold_s,
                       args.requote_s, args.maker_fee_bp, args.taker_fee_bp,
                       args.half_life_s)
        if r:
            per.append(r)
    if not per:
        print("无数据")
        return 1

    print("[1] 三种出场纪律（跨币按往返数加权）")
    print("    %-14s %7s %9s %9s %9s %8s %8s %9s"
          % ("模式", "往返数", "均值", "中位", "p5", "胜率", "强平", "持仓中位"))
    summary = {}
    for m in MODES:
        rows = [r["by_mode"][m] for r in per if r["by_mode"].get(m)]
        if not rows:
            continue
        N = sum(r["n"] for r in rows)
        mean = sum(r["net_bp_mean"] * r["n"] for r in rows) / N
        med = float(np.median([r["net_bp_median"] for r in rows]))
        p5 = float(np.median([r["p5"] for r in rows]))
        wr = sum(r["win_rate"] * r["n"] for r in rows) / N
        fr = sum(r["forced_rate"] * r["n"] for r in rows) / N
        hm = sum(r["hold_ms_median"] * r["n"] for r in rows) / N
        summary[m] = {"n": N, "net_bp_mean": mean, "net_bp_median_sym": med,
                      "p5_median": p5, "win_rate": wr, "forced_rate": fr,
                      "hold_ms_median": hm, "usd_per_rt_30": mean / 1e4 * 30}
        print("    %-14s %7d %+9.4f %+9.4f %+9.3f %7.1f%% %7.1f%% %8.0fms"
              % (m, N, mean, med, p5, 100 * wr, 100 * fr, hm))

    base = summary.get("E0 向下追价", {})
    print("\n[2] 相对 E0（向下追价）")
    for m in MODES[1:]:
        v = summary.get(m)
        if not v or not base:
            continue
        print("    %-14s 均值改善 %+.4fbp   p5 改善 %+.3fbp   强平 %+.1fpp"
              % (m, v["net_bp_mean"] - base["net_bp_mean"],
                 v["p5_median"] - base["p5_median"],
                 100 * (v["forced_rate"] - base["forced_rate"])))

    print("\n[3] 判据（事先定死：均值转正 且 强平<30%）")
    best = max(summary.items(), key=lambda kv: kv[1]["net_bp_mean"])
    v = best[1]
    if v["net_bp_mean"] > 0 and v["forced_rate"] < 0.30:
        print("    ⇒ ✓ **出场纪律就是解**：%s 均值 %+.4fbp，强平 %.1f%%"
              % (best[0], v["net_bp_mean"], 100 * v["forced_rate"]))
        print("       每 $30 腿 ≈ %+.6f 美元/往返" % v["usd_per_rt_30"])
    elif v["net_bp_mean"] > base.get("net_bp_mean", -9e9):
        print("    ⇒ ~ %s 有改善（%+.4fbp）但均值仍为负或强平过高（%.1f%%）"
              % (best[0], v["net_bp_mean"], 100 * v["forced_rate"]))
        print("       方向对，需调 max_hold / 追价起点")
    else:
        print("    ⇒ ✗ 无改善 ⇒ 亏损不来自向下追价，需另找")

    print("\n[4] 分币明细")
    for m in MODES:
        print("    --- %s ---" % m)
        for r in per:
            v = r["by_mode"].get(m)
            if not v:
                continue
            print("      %-8s n=%5d  均值 %+8.4fbp  中位 %+8.4fbp  p5 %+8.3f  "
                  "胜率 %5.1f%%  强平 %5.1f%%"
                  % (r["symbol"], v["n"], v["net_bp_mean"], v["net_bp_median"],
                     v["p5"], 100 * v["win_rate"], 100 * v["forced_rate"]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "hours": args.hours, "symbols": syms, "width_bp": args.width_bp,
        "max_hold_s": args.max_hold_s, "half_life_s": args.half_life_s,
        "per_symbol": per, "summary": summary,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
