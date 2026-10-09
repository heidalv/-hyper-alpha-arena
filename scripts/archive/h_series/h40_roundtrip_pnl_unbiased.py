"""H40：真实往返净额（修正选择偏差版）—— 平仓腿**持续重挂**，含卡住的亏损。

## H39 错在哪（必须记下，这是第 12 个口径错误）

H39 报「净额 +0.4416bp/往返，胜率 100%」，**全假的**，两个原因：

  ① **平仓腿锁死在入场时刻的最优卖价**上等 300 秒。
     入场卖价 > 入场买价 ⇒ 毛价差**恒为正** ⇒ 胜率必然 100%。这不是测量，是同义反复。
  ② **卡住的往返（52.4%）被排除在净额之外** —— 而"卡住"恰恰是
     **价格朝不利方向走**（涨不回去）的那些 ⇒ 典型的**选择偏差**：
     只统计了赢的那一半。

## 真实引擎怎么做

平仓腿**每个 tick 都按当前最优价重挂**（我们的 `min_width_reduce_bp=0` 就是让它贴盘口）。
所以它不会锁着一个好价不动，而是**跟着市场走** ⇒ 亏损会在那里兑现。

## 本脚本的做法

    ① 入场腿：挂在最优买价，队列消耗制成交（LA = 该档真实挂量）
    ② 入场成交后，**进入持仓**；此后每 `requote_s` 秒把平仓腿
       重挂在**当前**最优卖价上（LA = 当前该档挂量）
    ③ 平仓腿成交 ⇒ 往返完成，净额 = (出场价 − 入场价)/入场价 × 1e4
    ④ 超过 `max_hold_s` 仍未平 ⇒ **按当时中价强制平掉**（模拟 taker 平仓，含 4bp 费）
       —— **这一条是关键**：它把"卡住"的亏损真的记进去了，不再排除

## 判据（事先定死）

  · 净额均值 > 0 且 中位 > 0 ⇒ 正收益
  · 强平占比 > 30% ⇒ 出场机制不可接受（即使净额为正）
  · 与 H39 对照：若本版显著低于 H39，**差额就是选择偏差的幅度**

用法：
    .venv\\Scripts\\python.exe scripts\\h40_roundtrip_pnl_unbiased.py --hours 24
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

OUT = ROOT / "research_l1" / "out" / "h40_roundtrip_pnl_unbiased.json"


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def run_symbol(s, hours, quote_every_s, entry_wait_s, requote_s, max_hold_s,
               maker_fee_bp, taker_fee_bp, log, width_bp=0.0):
    import numpy as np
    import psycopg2
    import psycopg2.extras

    vs = s if s.endswith("USDT") else f"{s}USDT"
    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    since = f"(extract(epoch from now())*1000)::bigint - {int(hours*3600_000)}"
    cur.execute(
        "SELECT event_ts_ms, (bids->0->>0)::float bp, (bids->0->>1)::float bq,"
        "       (asks->0->>0)::float ap, (asks->0->>1)::float aq"
        f"  FROM asterdex_depth_snapshots WHERE symbol=%s AND event_ts_ms > {since}"
        " ORDER BY event_ts_ms", (vs,))
    dep = cur.fetchall()
    cur.execute(
        "SELECT event_ts_ms, price::float p, qty::float q, is_buyer_maker ibm"
        f"  FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > {since}"
        " ORDER BY event_ts_ms", (vs,))
    tr = cur.fetchall()
    cn.close()
    if len(dep) < 2000 or len(tr) < 1000:
        return None

    dts = np.array([int(x["event_ts_ms"]) for x in dep], dtype=np.int64)
    dbp = np.array([float(x["bp"] or 0) for x in dep])
    dbq = np.array([float(x["bq"] or 0) for x in dep])
    dap = np.array([float(x["ap"] or 0) for x in dep])
    daq = np.array([float(x["aq"] or 0) for x in dep])
    dmid = (dbp + dap) / 2.0
    tts = np.array([int(x["event_ts_ms"]) for x in tr], dtype=np.int64)
    tpx = np.array([float(x["p"]) for x in tr])
    tq = np.array([float(x["q"]) for x in tr])
    tsell = np.array([bool(x["ibm"]) for x in tr])

    def cum_fill(px, la, t_from, t_to, want_sell):
        """[t_from, t_to) 内累计对手方主动量 ≥ la 的第一个成交下标；无则 -1。"""
        j0 = int(np.searchsorted(tts, t_from, "left"))
        j1 = int(np.searchsorted(tts, t_to, "left"))
        cum = 0.0
        for jj in range(j0, min(j1, len(tts))):
            if tsell[jj] != want_sell:
                continue
            if abs(tpx[jj] - px) / px * 1e4 > 1.0:
                continue
            cum += float(tq[jj])
            if cum >= la:
                return jj
        return -1

    step = max(1, int(quote_every_s * 1000 / max(1, int(np.median(np.diff(dts)) or 2000))))
    qidx = np.arange(0, len(dts), step, dtype=np.int64)
    ew = int(entry_wait_s * 1000)
    rq = max(1000, int(requote_s * 1000))
    mh = int(max_hold_s * 1000)

    rts, no_entry = [], 0
    for i in qidx:
        # [H41] 挂宽：入场腿挂在最优买价**之外** width_bp（真实可下单的价）
        raw_bid, la_b = float(dbp[i]), float(dbq[i])
        if raw_bid <= 0 or la_b <= 0:
            continue
        bid = raw_bid * (1.0 - width_bp / 1e4)
        # 挂更深 ⇒ 该档没有别人的队列 ⇒ LA=0；代价是价格要**走得更远**
        la_entry = 0.0 if width_bp > 0 else la_b
        t0 = int(dts[i])
        je = cum_fill(bid, la_entry, t0, t0 + ew, True)
        if je < 0:
            no_entry += 1
            continue
        t_entry = int(tts[je])
        entry_px = bid
        # ── 持仓：每 requote_s 重挂平仓腿于**当前**最优卖价 ────────────
        t_cur = t_entry
        exit_px = None
        t_exit = None
        deadline = t_entry + mh
        while t_cur < deadline:
            di = int(np.searchsorted(dts, t_cur, "right")) - 1
            if di < 0 or float(dap[di]) <= 0 or float(daq[di]) <= 0:
                t_cur += rq
                continue
            # [H41] 出场腿同样挂在最优卖价**之外** width_bp
            ask = float(dap[di]) * (1.0 + width_bp / 1e4)
            la_a = 0.0 if width_bp > 0 else float(daq[di])
            t_next = min(t_cur + rq, deadline)
            jx = cum_fill(ask, la_a, t_cur, t_next, False)
            if jx >= 0:
                exit_px = ask
                t_exit = int(tts[jx])
                break
            t_cur = t_next
        if exit_px is None:
            # 超时 ⇒ taker 强平，按当时中价（对手价成交，付 taker 费）
            di = int(np.searchsorted(dts, deadline, "right")) - 1
            if di < 0:
                continue
            m = float(dmid[di])
            if m <= 0:
                continue
            fill_px = m * (1.0 - taker_fee_bp / 1e4)   # 卖出，费率按 bp 扣在价上
            net = (fill_px - entry_px) / entry_px * 1e4 - maker_fee_bp
            rts.append({"gross_bp": net + maker_fee_bp, "net_bp": net,
                        "hold_ms": deadline - t_entry, "forced": True})
        else:
            gross = (exit_px - entry_px) / entry_px * 1e4
            net = gross - 2 * maker_fee_bp
            rts.append({"gross_bp": gross, "net_bp": net,
                        "hold_ms": t_exit - t_entry, "forced": False})

    if not rts:
        log(f"  {s:<8} 无往返（未成交 {no_entry}）")
        return None
    n = np.array([r["net_bp"] for r in rts])
    h = np.array([r["hold_ms"] for r in rts])
    forced = np.array([r["forced"] for r in rts])
    return {
        "symbol": s, "n_roundtrips": len(rts), "n_no_entry": no_entry,
        "net_bp_mean": float(n.mean()), "net_bp_median": float(np.median(n)),
        "win_rate": float((n > 0).mean()),
        "hold_ms_median": float(np.median(h)),
        "forced_rate": float(forced.mean()),
        "forced_net_mean": float(n[forced].mean()) if forced.any() else None,
        "passive_net_mean": float(n[~forced].mean()) if (~forced).any() else None,
        "usd_per_rt_30": float(n.mean() / 1e4 * 30),
    }


def main() -> int:
    import numpy as np

    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--symbols", default="BTC,ETH,SOL,XRP,ASTER")
    ap.add_argument("--quote-every-s", type=float, default=15.0)
    ap.add_argument("--entry-wait-s", type=float, default=120.0)
    ap.add_argument("--requote-s", type=float, default=15.0,
                    help="平仓腿重挂间隔（真实引擎 15s tick）")
    ap.add_argument("--max-hold-s", type=float, default=300.0,
                    help="超过则 taker 强平（与线上 max_one_side_seconds 一致）")
    ap.add_argument("--maker-fee-bp", type=float, default=0.0)
    ap.add_argument("--taker-fee-bp", type=float, default=4.0)
    ap.add_argument("--width-bp", type=float, default=0.0,
                    help="挂宽（bp）：两侧都挂在最优价之外该值；0=贴 touch")
    args = ap.parse_args()
    syms = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]

    print("H40 真实往返净额（修正选择偏差：平仓腿持续重挂 + 超时强平计入）")
    print(f"窗口={args.hours}h  币={len(syms)}  入场等待={args.entry_wait_s:.0f}s")
    print(f"挂宽={args.width_bp:.2f}bp  平仓重挂间隔={args.requote_s:.0f}s  最长持有={args.max_hold_s:.0f}s  "
          f"maker={args.maker_fee_bp:+.2f}bp  taker={args.taker_fee_bp:.2f}bp\n")

    res = []
    for s in syms:
        r = run_symbol(s, args.hours, args.quote_every_s, args.entry_wait_s,
                       args.requote_s, args.max_hold_s,
                       args.maker_fee_bp, args.taker_fee_bp, print,
                       width_bp=args.width_bp)
        if r:
            res.append(r)
            print("  %-8s 往返 %5d  净 %+7.4fbp  中位 %+7.4fbp  胜率 %4.1f%%  "
                  "持仓中位 %6.0fms  强平 %4.1f%%  被动腿 %+7.4f  强平腿 %+7.4f"
                  % (r["symbol"], r["n_roundtrips"], r["net_bp_mean"],
                     r["net_bp_median"], 100 * r["win_rate"], r["hold_ms_median"],
                     100 * r["forced_rate"],
                     r["passive_net_mean"] if r["passive_net_mean"] is not None else 0.0,
                     r["forced_net_mean"] if r["forced_net_mean"] is not None else 0.0))
    if not res:
        print("\n无往返")
        return 1

    N = sum(r["n_roundtrips"] for r in res)
    net = sum(r["net_bp_mean"] * r["n_roundtrips"] for r in res) / N
    med = float(np.median([r["net_bp_median"] for r in res]))
    wr = sum(r["win_rate"] * r["n_roundtrips"] for r in res) / N
    fr = sum(r["forced_rate"] * r["n_roundtrips"] for r in res) / N
    hr = sum(r["hold_ms_median"] * r["n_roundtrips"] for r in res) / N

    print("\n[汇总] 跨币按往返数加权")
    print("    完整往返数        %d" % N)
    print("    **净额/往返       %+.4f bp**" % net)
    print("    净额中位（各币）  %+.4f bp" % med)
    print("    胜率              %.1f%%" % (100 * wr))
    print("    持仓中位          %.0f ms" % hr)
    print("    **强平占比        %.1f%%**" % (100 * fr))
    print("    ⇒ 每 $30 腿 ≈ %+.6f 美元/往返" % (net / 1e4 * 30))

    print("\n[与 H39 对照]")
    print("    H39（有偏）：净 +0.4416bp/往返，胜率 100.0%，卡住 52.4%（被排除）")
    print("    H40（无偏）：净 %+.4fbp/往返，胜率 %.1f%%，强平 %.1f%%（**已计入**）"
          % (net, 100 * wr, 100 * fr))
    print("    ⇒ **差额 %+.4fbp 就是选择偏差的幅度**" % (net - 0.4416))

    print("\n[判据]")
    if net > 0 and med > 0:
        print("    ⇒ ✓ 净额均值与中位都为正 ⇒ **正收益**")
    elif net > 0:
        print("    ⇒ ~ 均值为正、中位为负 ⇒ 靠少数赢利，不稳")
    else:
        print("    ⇒ ✗ 净额为负 ⇒ 该配置不成立")
    if fr > 0.30:
        print("    ⇒ ✗ 强平占比 %.1f%% > 30%% ⇒ **出场机制不可接受**（即使净额为正）" % (100 * fr))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "hours": args.hours, "symbols": syms, "requote_s": args.requote_s,
        "max_hold_s": args.max_hold_s, "maker_fee_bp": args.maker_fee_bp,
        "taker_fee_bp": args.taker_fee_bp,
        "per_symbol": res,
        "summary": {"n": N, "net_bp_mean": net, "net_bp_median_sym": med,
                    "win_rate": wr, "forced_rate": fr, "hold_ms_median": hr,
                    "usd_per_rt_30": net / 1e4 * 30},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
