"""H47：队列位置的真实代价 —— "排在队尾" vs 当前模型的"假设队首"。

## 为什么要做（H19 的 +0.42bp@1s 还没接进往返口径）

H19 用真实逐笔实测：**队首 − 队尾 = +0.42bp @1s**，与我们整个净边际（−0.60bp）同量级。
但那是在**单腿 markout** 口径下测的，**从未接进 H43/H46 的往返框架**。

而当前 H43/H46 的成交判定有一个隐含假设：
**只要价格走到我们的价位且有对手方量，就成交** —— 这等价于**假设我们排在队首**。

## 真实机制（论文口径，Albers et al. 第 612–618 行）

挂着买单时，成交条件不是"价格穿过"，而是
    **自挂单以来，该价位的累计对手方主动量 ≥ 我们前面的挂量 LA**
而我们新挂的单必然排在**队尾** ⇒ `LA = 挂单时刻该价位的全部挂量`。

## 两种口径对照

    **Q0 队首假设**（现状）：`LA = 0` —— 价格走到就成交
    **Q1 队尾真实**：`LA = book_ticker 的 bid_qty`（挂单时刻该价位挂量）
        ⇒ 累计主动卖量必须**吃穿整个队列**才轮到我们

`LA` 用 `asterdex_book_ticker.bid_qty`（最优档真实挂量，含我们自己的量可忽略）。

## 成交后出场腿同样处理（挂在最优卖价，LA = ask_qty）

## 判据（事先定死）

  · Q1 若比 Q0 **更差** ⇒ 当前模型偏乐观，H46 的 +0.6358bp 需要下调
  · Q1 若比 Q0 **更好** ⇒ 反直觉，需查是否引入了别的偏差
  · 同时报**成交率**：队尾假设会大幅降低成交率

用法：
    .venv\\Scripts\\python.exe scripts\\h47_queue_position_roundtrip.py --hours 24
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

OUT = ROOT / "research_l1" / "out" / "h47_queue_position.json"
MODES = ("Q0 队首假设", "Q1 队尾真实")


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
        i = int(np.searchsorted(kts, t_ms, "right")) - 1
        return i

    # 报价时点（按 quote_every_s 取 book_ticker 快照）
    step = max(1, int(quote_every_s * 1000 / max(1, int(np.median(np.diff(kts)) or 100))))
    qidx = np.arange(0, len(kts), step, dtype=np.int64)
    max_ms = int(max_hold_s * 1000)
    rq = max(1000, int(requote_s * 1000))

    res = {m: {"pnl": [], "holds": [], "forced": 0, "fill": 0, "dec": 0}
           for m in MODES}
    for i in qidx:
        bid = float(kbid[i])
        if bid <= 0:
            continue
        t0 = int(kts[i])
        px_buy = bid * (1.0 - w_bp / 1e4)
        la_q0 = 0.0
        la_q1 = float(kbq[i])                    # 队尾：前面有整个队列
        for m in MODES:
            res[m]["dec"] += 1
        # ① 入场腿：两种 LA 各判一次
        def try_entry(la):
            j0 = int(np.searchsorted(tts, t0, "left"))
            j1 = int(np.searchsorted(tts, t0 + max_ms, "left"))
            cum = 0.0
            for jj in range(j0, min(j1, len(tts))):
                if tsell[jj] and abs(tpx[jj] - px_buy) / px_buy * 1e4 < 1.0:
                    cum += float(tq[jj])
                    if cum >= la:
                        return jj
            return -1

        je0 = try_entry(la_q0)
        je1 = try_entry(la_q1)
        for m, je in (("Q0 队首假设", je0), ("Q1 队尾真实", je1)):
            if je < 0:
                continue
            t_entry = int(tts[je])
            entry_px = float(tpx[je])
            ii = iat(t_entry)
            if ii < 0 or kask[ii] <= 0 or kaq[ii] <= 0:
                continue
            target = float(kask[ii]) * (1.0 + w_bp / 1e4)
            la_exit = 0.0 if m == "Q0 队首假设" else float(kaq[ii])
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
                res[m]["pnl"].append(net)
                res[m]["holds"].append(deadline - t_entry)
                res[m]["forced"] += 1
            else:
                net = (exit_px - entry_px) / entry_px * 1e4 - 2 * maker_fee_bp
                res[m]["pnl"].append(net)
                res[m]["holds"].append(t_exit - t_entry)
            res[m]["fill"] += 1

    out = {"symbol": s, "by_mode": {}}
    for m in MODES:
        d = res[m]
        if not d["pnl"]:
            out["by_mode"][m] = None
            continue
        p = np.array(d["pnl"])
        out["by_mode"][m] = {
            "n": len(p), "n_dec": d["dec"], "n_forced": d["forced"],
            "fill_rate": d["fill"] / max(1, d["dec"]),
            "net_bp_mean": float(p.mean()), "net_bp_median": float(np.median(p)),
            "p5": float(np.percentile(p, 5)),
            "win_rate": float((p > 0).mean()),
            "hold_ms_median": float(np.median(d["holds"])),
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
    ap.add_argument("--maker-fee-bp", type=float, default=-0.5)
    ap.add_argument("--taker-fee-bp", type=float, default=4.0)
    args = ap.parse_args()
    syms = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]

    print("H47 队列位置的真实代价（队尾 vs 队首假设）")
    print(f"窗口={args.hours}h  币={len(syms)}  挂宽={args.width_bp}bp  "
          f"maker={args.maker_fee_bp:+.2f}bp  最长持有={args.max_hold_s:.0f}s\n")

    per = []
    for s in syms:
        r = run_symbol(s, args.hours, args.width_bp, args.quote_every_s, args.max_hold_s,
                       args.requote_s, args.maker_fee_bp, args.taker_fee_bp)
        if r:
            per.append(r)
    if not per:
        print("无数据")
        return 1

    print("[1] 两种口径")
    print("    %-14s %8s %9s %10s %9s %9s %8s %8s"
          % ("模式", "决策数", "成交率", "均值", "中位", "p5", "胜率", "强平率"))
    summary = {}
    for m in MODES:
        rows = [r["by_mode"][m] for r in per if r["by_mode"].get(m)]
        if not rows:
            continue
        N = sum(r["n"] for r in rows)
        dec = sum(r["n_dec"] for r in rows)
        fr = sum(r["fill_rate"] * r["n_dec"] for r in rows) / max(1, dec)
        mean = sum(r["net_bp_mean"] * r["n"] for r in rows) / N
        med = float(np.median([r["net_bp_median"] for r in rows]))
        p5 = float(np.median([r["p5"] for r in rows]))
        wr = sum(r["win_rate"] * r["n"] for r in rows) / N
        fo = sum(r["forced_rate"] * r["n"] for r in rows) / N
        summary[m] = {"n": N, "n_dec": dec, "fill_rate": fr, "net_bp_mean": mean,
                      "net_bp_median_sym": med, "p5_median": p5, "win_rate": wr,
                      "forced_rate": fo, "usd_per_rt_30": mean / 1e4 * 30}
        print("    %-14s %8d %8.2f%% %+10.4f %+9.4f %+9.3f %7.1f%% %7.1f%%"
              % (m, dec, 100 * fr, mean, med, p5, 100 * wr, 100 * fo))

    print("\n[2] 队尾相对队首")
    a, b = summary.get("Q0 队首假设"), summary.get("Q1 队尾真实")
    if a and b:
        print("    均值差 %+.4fbp   成交率差 %+.2fpp   中位差 %+.4fbp"
              % (b["net_bp_mean"] - a["net_bp_mean"],
                 100 * (b["fill_rate"] - a["fill_rate"]),
                 b["net_bp_median_sym"] - a["net_bp_median_sym"]))
        print("    ⇒ %s" % ("**队尾更差 ⇒ 当前模型偏乐观**"
                            if b["net_bp_mean"] < a["net_bp_mean"]
                            else "队尾反而更好 —— 需查是否引入了别的偏差"))

    print("\n[3] 判据（事先定死）")
    if a and b:
        print("    H46 报的是 Q0 口径（队首假设）：+0.6358bp")
        print("    Q1 口径（队尾真实）：%+.4fbp" % b["net_bp_mean"])
        if b["net_bp_mean"] > 0:
            print("    ⇒ ✓ **队尾假设下仍为正** ⇒ H46 的结论稳健")
        else:
            print("    ⇒ ✗ **队尾假设下转负** ⇒ H46 的 +0.6358bp 需按队尾口径下调")

    print("\n[4] 分币")
    for r in per:
        for m in MODES:
            v = r["by_mode"].get(m)
            if not v:
                continue
            print("    %-8s %-13s 成交率 %6.2f%%  均值 %+8.4fbp  中位 %+8.4fbp  "
                  "p5 %+8.3f  胜率 %5.1f%%"
                  % (r["symbol"], m, 100 * v["fill_rate"], v["net_bp_mean"],
                     v["net_bp_median"], v["p5"], 100 * v["win_rate"]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "hours": args.hours, "symbols": syms, "width_bp": args.width_bp,
        "maker_fee_bp": args.maker_fee_bp, "per_symbol": per, "summary": summary,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
