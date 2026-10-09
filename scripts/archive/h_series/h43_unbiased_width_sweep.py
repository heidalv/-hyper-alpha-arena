"""H43：把未完成的双扫按 taker 强平计入 —— 给出 w 扫描的**无偏**净额。

## 为什么要做（H42 留下的偏差）

H42 用真实逐笔成交价验证了双扫（w=0.5）：

    完成率 88.4%（23389 个完整双扫）
    间隔中位 7784ms
    净额 **+0.9099 bp/往返**（真实成交价）

**但它排除了 12% 未完成的往返** —— 而那些正是价格单边走、等不到对手腿的。
H40 的做法（超时按中价 taker 强平并计入）才是无偏的。

## 本脚本 = H42 的成交判据 + H40 的强平计入

    ① 入场：第一笔打到 `bid × (1 − w/1e4)` 的主动卖 ⇒ 按**真实成交价**成交
    ② 出场：自入场起，第一笔打到 `ask_t × (1 + w/1e4)` 的主动买
       （`ask_t` = 入场时刻的最优卖价；**每 `requote_s` 用新的 ask_t 重挂**）
    ③ 超时（`max_hold_s`）仍无出场 ⇒ 按当时中价 taker 强平，**计入**
    ④ 净额 = (出场真实成交价 − 入场真实成交价)/入场价 × 1e4 − 手续费

## 判据（事先定死）

  · 净额 > 0 且 强平占比 < 30% ⇒ **该 w 可用**
  · 找出净额最大的 w
  · 与 H42（排除未完成）对照 ⇒ 差额就是"未完成"的代价

用法：
    .venv\\Scripts\\python.exe scripts\\h43_unbiased_width_sweep.py --hours 24
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

OUT = ROOT / "research_l1" / "out" / "h43_unbiased_width_sweep.json"


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def run_symbol(s, hours, w_bp, quote_every_s, max_hold_s, requote_s,
               maker_fee_bp, taker_fee_bp):
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

    pnl, holds, forced_flags, no_entry = [], [], [], 0
    for i in qidx:
        bid = float(dbp[i])
        if bid <= 0:
            continue
        px_buy = bid * (1.0 - w_bp / 1e4)
        t0 = int(dts[i])
        # ① 入场（真实成交价）
        j0 = int(np.searchsorted(tts, t0, "left"))
        j1 = int(np.searchsorted(tts, t0 + max_ms, "left"))
        je = -1
        for jj in range(j0, min(j1, len(tts))):
            if tsell[jj] and tpx[jj] <= px_buy * (1.0 + 1.0 / 1e4):
                je = jj
                break
        if je < 0:
            no_entry += 1
            continue
        t_entry = int(tts[je])
        entry_px = float(tpx[je])
        deadline = t_entry + max_ms
        # ② 出场：每 rq 用新的 ask 重挂
        t_cur = t_entry
        exit_px = None
        t_exit = None
        while t_cur < deadline:
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
            # ③ 超时 ⇒ taker 强平（按当时中价，扣 taker 费），**计入**
            m = mid_at(deadline)
            if m <= 0:
                continue
            net = (m * (1.0 - taker_fee_bp / 1e4) - entry_px) / entry_px * 1e4 - maker_fee_bp
            pnl.append(net)
            holds.append(deadline - t_entry)
            forced_flags.append(True)
        else:
            net = (exit_px - entry_px) / entry_px * 1e4 - 2 * maker_fee_bp
            pnl.append(net)
            holds.append(t_exit - t_entry)
            forced_flags.append(False)

    if not pnl:
        return None
    p = np.array(pnl)
    h = np.array(holds)
    f = np.array(forced_flags)
    return {
        "symbol": s, "w_bp": w_bp, "n": len(p), "n_no_entry": no_entry,
        "net_bp_mean": float(p.mean()), "net_bp_median": float(np.median(p)),
        "win_rate": float((p > 0).mean()),
        "hold_ms_median": float(np.median(h)),
        "forced_rate": float(f.mean()),
        "passive_net": float(p[~f].mean()) if (~f).any() else None,
        "forced_net": float(p[f].mean()) if f.any() else None,
        "usd_per_rt_30": float(p.mean() / 1e4 * 30),
    }


def main() -> int:
    import numpy as np

    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--symbols", default="BTC,ETH,SOL,XRP,ASTER")
    ap.add_argument("--widths", default="0.5,1.0,1.5,2.0")
    ap.add_argument("--quote-every-s", type=float, default=15.0)
    ap.add_argument("--requote-s", type=float, default=15.0)
    ap.add_argument("--max-hold-s", type=float, default=300.0)
    ap.add_argument("--maker-fee-bp", type=float, default=0.0)
    ap.add_argument("--taker-fee-bp", type=float, default=4.0)
    args = ap.parse_args()
    syms = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    widths = [float(x) for x in args.widths.split(",")]

    print("H43 无偏挂宽扫描（真实成交价 + 超时强平计入）")
    print(f"窗口={args.hours}h  币={len(syms)}  挂宽={widths}  "
          f"最长持有={args.max_hold_s:.0f}s  maker={args.maker_fee_bp:+.2f}bp\n")

    all_res = {}
    for w in widths:
        per = []
        for s in syms:
            r = run_symbol(s, args.hours, w, args.quote_every_s, args.max_hold_s,
                           args.requote_s, args.maker_fee_bp, args.taker_fee_bp)
            if r:
                per.append(r)
        if not per:
            continue
        N = sum(r["n"] for r in per)
        net = sum(r["net_bp_mean"] * r["n"] for r in per) / N
        med = float(np.median([r["net_bp_median"] for r in per]))
        wr = sum(r["win_rate"] * r["n"] for r in per) / N
        fr = sum(r["forced_rate"] * r["n"] for r in per) / N
        hm = sum(r["hold_ms_median"] * r["n"] for r in per) / N
        all_res[w] = {"per_symbol": per, "n": N, "net_bp_mean": net,
                      "net_bp_median_sym": med, "win_rate": wr,
                      "forced_rate": fr, "hold_ms_median": hm,
                      "usd_per_rt_30": net / 1e4 * 30}
        print("  w=%-5.1f 往返 %6d  净/往返 %+8.4fbp  中位 %+8.4fbp  胜率 %5.1f%%  "
              "强平 %5.1f%%  持仓中位 %7.0fms  $30腿 %+9.6f"
              % (w, N, net, med, 100 * wr, 100 * fr, hm, net / 1e4 * 30))

    if not all_res:
        print("无数据")
        return 1

    best_w = max(all_res, key=lambda k: all_res[k]["net_bp_mean"])
    b = all_res[best_w]
    print("\n[判定]")
    print("    最优挂宽 w=%.1fbp ⇒ 净 %+.4fbp/往返（强平 %.1f%%）"
          % (best_w, b["net_bp_mean"], 100 * b["forced_rate"]))
    if b["net_bp_mean"] > 0 and b["forced_rate"] < 0.30:
        print("    ⇒ ✓ **净额为正且强平 < 30%% ⇒ 该配置可用**")
    elif b["net_bp_mean"] > 0:
        print("    ⇒ ~ 净额为正但强平 %.1f%% ≥ 30%% ⇒ 出场机制需改进" % (100 * b["forced_rate"]))
    else:
        print("    ⇒ ✗ 所有挂宽的净额都 ≤ 0")

    print("\n    [与 H42 对照] H42（排除未完成）w=0.5: +0.9099bp，完成率 88.4%")
    if 0.5 in all_res:
        d = all_res[0.5]["net_bp_mean"] - 0.9099
        print("    H43（计入强平）w=0.5: %+.4fbp ⇒ **差额 %+.4fbp = 未完成的代价**"
              % (all_res[0.5]["net_bp_mean"], d))

    print("\n    [分币明细 @ w=%.1f]" % best_w)
    for r in b["per_symbol"]:
        print("      %-8s n=%5d  净 %+8.4fbp  胜率 %5.1f%%  强平 %5.1f%%  "
              "被动腿 %+8.4f  强平腿 %+8.4f"
              % (r["symbol"], r["n"], r["net_bp_mean"], 100 * r["win_rate"],
                 100 * r["forced_rate"],
                 r["passive_net"] if r["passive_net"] is not None else 0.0,
                 r["forced_net"] if r["forced_net"] is not None else 0.0))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "hours": args.hours, "symbols": syms, "widths": widths,
        "max_hold_s": args.max_hold_s, "maker_fee_bp": args.maker_fee_bp,
        "taker_fee_bp": args.taker_fee_bp, "by_width": {str(k): v for k, v in all_res.items()},
        "best_width": best_w,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
