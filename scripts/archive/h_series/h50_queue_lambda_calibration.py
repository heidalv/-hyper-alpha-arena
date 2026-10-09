"""H50：队列约束的真实强度 —— 把 H47 的"纯队尾"与实盘对齐。

## 必须解释的矛盾

    **H47 队尾口径**：成交率 67.8%，**强平率 54.0%**，均值 −2.1635bp
    **实盘实测**：3 笔强平 / 约 30 个仓位 ≈ **10%**（H21）

**若实盘真是 54%，车道早就暴露了。** ⇒ 说明"新单必然排队尾（LA = 全部挂量）"
这个假设**过于严苛**。

## 为什么可能过严

`LA = 挂单时刻该价位全部挂量` 意味着"我们前面有整整一个队列"。
但真实情况是：
  · 队列里**前面的单会被撤**（Aster 是 price-time FIFO，撤单不需要我们动手）
  · 队列**会随价格移动而整体消失**（价格一走，那个价位就空了）
  · 我们**每个 tick（15s）都重挂** ⇒ 每次重挂都重新排队，但也重新获得机会

⇒ 真实有效 LA 应介于 0（队首）与 `全部挂量`（队尾）之间。

## 本脚本做什么

扫描 **LA 系数 λ ∈ {0, 0.25, 0.5, 0.75, 1.0}**，其中 `LA = λ × 该档挂量`：

    λ = 0.0  ⇒ 队首（H46 的乐观口径）
    λ = 1.0  ⇒ 队尾（H47 的悲观口径）
    中间      ⇒ **未知的真实值**

并**用实盘的强平率（10%）作为标定锚点**：
找出哪个 λ 的强平率 ≈ 10% ⇒ **那个 λ 就代表我们真实的队列位置。**

## 判据（事先定死）

  · 找出强平率最接近 10% 的 λ ⇒ **该 λ 下的均值就是可信的策略期望**
  · 若没有任何 λ 给出 ≈10% 的强平率 ⇒ 模型缺少某个机制（如撤单），需重新建模

用法：
    .venv\\Scripts\\python.exe scripts\\h50_queue_lambda_calibration.py --hours 24
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

OUT = ROOT / "research_l1" / "out" / "h50_queue_lambda.json"
LAMBDAS = [0.0, 0.25, 0.5, 0.75, 1.0]
# 实盘标定锚点：H21 实测 3 笔强平 / 约 30 个仓位
LIVE_FORCED_RATE = 0.10


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def run_symbol(s, hours, w_bp, quote_every_s, max_hold_s, requote_s,
               maker_fee_bp, taker_fee_bp, lams):
    import numpy as np
    import psycopg2
    import psycopg2.extras

    vs = s if s.endswith("USDT") else f"{s}USDT"
    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    since = f"(extract(epoch from now())*1000)::bigint - {int(hours*3600_000)}"
    cur.execute(
        "SELECT event_ts_ms, bid_px::float b, ask_px::float a,"
        "       bid_qty::float bq, ask_qty::float aq"
        f"  FROM asterdex_book_ticker WHERE symbol=%s AND event_ts_ms > {since}"
        " ORDER BY event_ts_ms", (vs,))
    tk = cur.fetchall()
    cur.execute(
        "SELECT event_ts_ms, price::float p, qty::float q, is_buyer_maker ibm"
        f"  FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > {since}"
        " ORDER BY event_ts_ms", (vs,))
    bt = cur.fetchall()
    cn.close()
    if len(tk) < 3000 or len(bt) < 1000:
        return None

    kts = np.array([int(x["event_ts_ms"]) for x in tk], dtype=np.int64)
    kbid = np.array([float(x["b"] or 0) for x in tk])
    kask = np.array([float(x["a"] or 0) for x in tk])
    kbq = np.array([float(x["bq"] or 0) for x in tk])
    kaq = np.array([float(x["aq"] or 0) for x in tk])
    kmid = (kbid + kask) / 2.0
    tts = np.array([int(x["event_ts_ms"]) for x in bt], dtype=np.int64)
    tpx = np.array([float(x["p"]) for x in bt])
    tq = np.array([float(x["q"]) for x in bt])
    tsell = np.array([bool(x["ibm"]) for x in bt])

    def iat(t_ms):
        return int(np.searchsorted(kts, t_ms, "right")) - 1

    step = max(1, int(quote_every_s * 1000 / max(1, int(np.median(np.diff(kts)) or 100))))
    qidx = np.arange(0, len(kts), step, dtype=np.int64)
    max_ms = int(max_hold_s * 1000)
    rq = max(1000, int(requote_s * 1000))

    res = {l: {"pnl": [], "holds": [], "forced": 0, "fill": 0, "dec": 0} for l in lams}
    for i in qidx:
        raw_bid = float(kbid[i])
        if raw_bid <= 0 or float(kask[i]) <= 0:
            continue
        px_buy = raw_bid * (1.0 - w_bp / 1e4)
        t0 = int(kts[i])
        for l in lams:
            res[l]["dec"] += 1
            la = l * float(kbq[i])
            j0 = int(np.searchsorted(tts, t0, "left"))
            j1 = int(np.searchsorted(tts, t0 + max_ms, "left"))
            cum = 0.0
            je = -1
            for jj in range(j0, min(j1, len(tts))):
                if tsell[jj] and abs(tpx[jj] - px_buy) / px_buy * 1e4 < 1.0:
                    cum += float(tq[jj])
                    if cum >= la:
                        je = jj
                        break
            if je < 0:
                continue
            t_entry = int(tts[je])
            entry_px = float(tpx[je])
            ii = iat(t_entry)
            if ii < 0 or kask[ii] <= 0 or kaq[ii] <= 0:
                continue
            target = float(kask[ii]) * (1.0 + w_bp / 1e4)
            la_exit = l * float(kaq[ii])
            deadline = t_entry + max_ms
            t_cur = t_entry
            exit_px = None
            t_exit = None
            while t_cur < deadline:
                t_next = min(t_cur + rq, deadline)
                j2 = int(np.searchsorted(tts, t_cur, "left"))
                j3 = int(np.searchsorted(tts, t_next, "left"))
                cum = 0.0
                for jj in range(j2, min(j3, len(tts))):
                    if (not tsell[jj]) and abs(tpx[jj] - target) / target * 1e4 < 1.0:
                        cum += float(tq[jj])
                        if cum >= la_exit:
                            exit_px = float(tpx[jj])
                            t_exit = int(tts[jj])
                            break
                if exit_px is not None:
                    break
                t_cur = t_next
            if exit_px is None:
                ii2 = iat(deadline)
                if ii2 < 0:
                    continue
                mm = float(kmid[ii2])
                if mm <= 0:
                    continue
                net = (mm * (1.0 - taker_fee_bp / 1e4) - entry_px) / entry_px * 1e4 - maker_fee_bp
                res[l]["pnl"].append(net)
                res[l]["holds"].append(deadline - t_entry)
                res[l]["forced"] += 1
            else:
                net = (exit_px - entry_px) / entry_px * 1e4 - 2 * maker_fee_bp
                res[l]["pnl"].append(net)
                res[l]["holds"].append(t_exit - t_entry)
            res[l]["fill"] += 1

    out = {"symbol": s, "by_lambda": {}}
    for l in lams:
        d = res[l]
        if not d["pnl"]:
            out["by_lambda"][str(l)] = None
            continue
        p = np.array(d["pnl"])
        out["by_lambda"][str(l)] = {
            "lambda": l, "n": len(p), "n_dec": d["dec"], "n_forced": d["forced"],
            "fill_rate": d["fill"] / max(1, d["dec"]),
            "net_bp_mean": float(p.mean()), "net_bp_median": float(np.median(p)),
            "p5": float(np.percentile(p, 5)),
            "win_rate": float((p > 0).mean()),
            "forced_rate": d["forced"] / max(1, len(p)),
        }
    return out


def main() -> int:
    import numpy as np

    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--symbols", default="BTC,ETH")
    ap.add_argument("--width-bp", type=float, default=0.5)
    ap.add_argument("--quote-every-s", type=float, default=15.0)
    ap.add_argument("--requote-s", type=float, default=15.0)
    ap.add_argument("--max-hold-s", type=float, default=300.0)
    ap.add_argument("--maker-fee-bp", type=float, default=0.0)
    ap.add_argument("--taker-fee-bp", type=float, default=4.0)
    args = ap.parse_args()
    syms = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]

    print("H50 队列约束强度标定（用实盘强平率 10% 作锚点）")
    print(f"窗口={args.hours}h  币={len(syms)}  挂宽={args.width_bp}bp  "
          f"maker={args.maker_fee_bp:+.2f}bp  LA = λ × 该档挂量\n")

    per = []
    for s in syms:
        r = run_symbol(s, args.hours, args.width_bp, args.quote_every_s, args.max_hold_s,
                       args.requote_s, args.maker_fee_bp, args.taker_fee_bp, LAMBDAS)
        if r:
            per.append(r)
    if not per:
        print("无数据")
        return 1

    print("[1] 各 λ 口径")
    print("    %6s %8s %9s %10s %9s %9s %8s %10s"
          % ("λ", "决策数", "成交率", "均值", "中位", "p5", "胜率", "**强平率**"))
    summary = {}
    for l in LAMBDAS:
        rows = [r["by_lambda"][str(l)] for r in per if r["by_lambda"].get(str(l))]
        if not rows:
            continue
        N = sum(r["n"] for r in rows)
        dec = sum(r["n_dec"] for r in rows)
        mean = sum(r["net_bp_mean"] * r["n"] for r in rows) / N
        med = float(np.median([r["net_bp_median"] for r in rows]))
        p5 = float(np.median([r["p5"] for r in rows]))
        wr = sum(r["win_rate"] * r["n"] for r in rows) / N
        fr = sum(r["forced_rate"] * r["n"] for r in rows) / N
        fillr = sum(r["fill_rate"] * r["n_dec"] for r in rows) / max(1, dec)
        summary[l] = {"n": N, "n_dec": dec, "fill_rate": fillr,
                      "net_bp_mean": mean, "net_bp_median_sym": med, "p5_median": p5,
                      "win_rate": wr, "forced_rate": fr,
                      "usd_per_rt_30": mean / 1e4 * 30}
        print("    %6.2f %8d %8.2f%% %+10.4f %+9.4f %+9.3f %7.1f%% %9.1f%%"
              % (l, dec, 100 * fillr, mean, med, p5, 100 * wr, 100 * fr))

    print(f"\n[2] 用实盘锚点标定（实盘强平率 ≈ {100*LIVE_FORCED_RATE:.0f}%）")
    best_l, best_d = None, 9e9
    for l, v in summary.items():
        d = abs(v["forced_rate"] - LIVE_FORCED_RATE)
        if d < best_d:
            best_l, best_d = l, d
    if best_l is not None:
        v = summary[best_l]
        print("    最接近的 λ = **%.2f**（强平 %.1f%%，与实盘差 %.1fpp）"
              % (best_l, 100 * v["forced_rate"], 100 * best_d))
        print("    ⇒ **该口径下的均值 = %+.4f bp/往返**  ← 这才是可信的策略期望"
              % v["net_bp_mean"])
        print("      中位 %+.4f   p5 %+.3f   胜率 %.1f%%   成交率 %.1f%%"
              % (v["net_bp_median_sym"], v["p5_median"], 100 * v["win_rate"],
                 100 * v["fill_rate"]))
        if best_d > 0.15:
            print("    ⚠️ 但最强接近的 λ 仍与实盘差 %.1fpp ⇒ **模型缺少某个机制**"
                  % (100 * best_d))
            print("      最可能是：队列里的**撤单**（我们前面的人撤了，我们自动前移）。")
            print("      本模型只让'成交'消耗队列，不让'撤单'消耗 ⇒ 系统性高估 LA。")

    print("\n[3] 与 H46/H47 对照")
    if 0.0 in summary and 1.0 in summary:
        print("    H46（= λ=0 队首）：%+.4fbp   强平 %.1f%%"
              % (summary[0.0]["net_bp_mean"], 100 * summary[0.0]["forced_rate"]))
        print("    H47（= λ=1 队尾）：%+.4fbp   强平 %.1f%%"
              % (summary[1.0]["net_bp_mean"], 100 * summary[1.0]["forced_rate"]))
        print("    ⇒ 两个口径之间**没有台阶**（H48 的结论），但强平率随 λ 单调上升")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "hours": args.hours, "symbols": syms, "lambdas": LAMBDAS,
        "maker_fee_bp": args.maker_fee_bp, "live_anchor": LIVE_FORCED_RATE,
        "per_symbol": per, "summary": {str(k): v for k, v in summary.items()},
        "calibrated_lambda": best_l,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
