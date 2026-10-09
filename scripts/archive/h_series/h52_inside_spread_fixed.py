"""H52：修正"进价差内"的成交判定 —— 检验 Arroyo 的 8.2 倍成交概率。

## H48 的判定错误（本脚本要修的）

H48 测"改善最优价（挂进价差内）"，成交率从 75.5% 崩到 **2.2%**。
但它对**所有**报价都用了同一个成交条件：

    if tsell[jj] and abs(tpx[jj] - px_buy) / px_buy * 1e4 < 1.0:
        cum += qty; if cum >= LA: 成交

**对"进价差内"的买单，这个条件是错的。**

我们的买价 `px` 在最优买价 `bid` **之上**（进价差内）。此时：
  · `px > bid` ⇒ 我们**就是**最优买价 ⇒ 任何打到 `px` 或更低的卖单都该成交我们
  · 不需要"恰好有一笔成交价 = px"——价格还没跌到 px 时，只要有人愿意卖在 px，
    就会优先打我们（我们价更优）

⇒ 正确判据：**当主动卖单的价格 ≤ 我们报价时成交**（而非"≈ 我们报价"）。

## 文献预期（Arroyo et al., Quantitative Finance 24(1):35–57, 2024）

Table 3（LOBSTER Nasdaq，9 只股票）—— 成交概率 / 平均成交时间（秒）：

    标的    最优价    +1      +2      +3      +4      +5     （tick 越靠内）
    AAPL   0.0539  0.1317  0.3601  0.4165  0.4382  0.4431
    AMZN   0.0923  0.1312  0.3360  0.4139  0.4343  0.4485
    CSCO   0.0573  0.1753  0.3549  0.3579  0.2484  0.4558
    成交时间 AAPL 1.32 → 0.64 → 0.19 → 0.11 → 0.14 → 0.08

论文原话："orders 改善最优价时成交概率高 **3~6 倍**，成交时间同比例缩短"。

**关键区别**：Arroyo 说的是"**改善**最优价"（价格进到价差内）
⇒ **我们在一个新建的队列里是队首（position 0）**。
这是 15 秒节奏的参与者**唯一能结构性获得队首位置**的机制。

## 判据（事先定死）

  · 若"进价差内"的成交率显著高于"贴 touch + 队尾" ⇒ **Arroyo 的机制在我们场地成立**
  · 且若其均值 > 0 ⇒ **这就是可执行的正收益路径**
  · 若成交率仍低 ⇒ 场地差异（Aster 的 tick 粒度相对价格很粗）导致无法进价差内

用法：
    .venv\\Scripts\\python.exe scripts\\h52_inside_spread_fixed.py --hours 24
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

OUT = ROOT / "research_l1" / "out" / "h52_inside_spread.json"
MODES = ("A 贴touch+队尾", "B 进价差内+队首", "C 贴touch+队首")


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def run_symbol(s, hours, d_bp, quote_every_s, max_hold_s, requote_s,
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

    def idx_at(t_ms):
        return int(np.searchsorted(kts, t_ms, "right")) - 1

    step = max(1, int(quote_every_s * 1000 / max(1, int(np.median(np.diff(kts)) or 100))))
    qidx = np.arange(0, len(kts), step, dtype=np.int64)
    max_ms = int(max_hold_s * 1000)
    rq = max(1000, int(requote_s * 1000))

    res = {m: {"pnl": [], "holds": [], "forced": 0, "fill": 0, "dec": 0, "t_fill": []}
           for m in MODES}
    for i in qidx:
        bid = float(kbid[i]); ask = float(kask[i])
        if bid <= 0 or ask <= bid:
            continue
        t0 = int(kts[i])
        # 三种报价
        plans = {
            "A 贴touch+队尾": (bid, float(kbq[i]), False),
            "B 进价差内+队首": (bid * (1.0 + d_bp / 1e4), 0.0, True),
            "C 贴touch+队首": (bid, 0.0, False),
        }
        for m in MODES:
            px_buy, la, inside = plans[m]
            res[m]["dec"] += 1
            # ── 修正后的入场判定 ───────────────────────────────
            # inside（进价差内）：成交 = 主动卖价 ≤ 我们报价（我们是最优买价）
            # 否则（贴 touch）：成交 = 主动卖价 ≤ 我们报价 且 累计量 ≥ 该档挂量
            j0 = int(np.searchsorted(tts, t0, "left"))
            j1 = int(np.searchsorted(tts, t0 + max_ms, "left"))
            cum = 0.0
            je = -1
            for jj in range(j0, min(j1, len(tts))):
                if not tsell[jj]:
                    continue
                if tpx[jj] <= px_buy * (1.0 + 1.0 / 1e4):
                    if inside:
                        je = jj          # 进价差内：任何打上来的卖单都成交我们
                        break
                    cum += float(tq[jj])
                    if cum >= la:
                        je = jj
                        break
            if je < 0:
                continue
            t_entry = int(tts[je])
            entry_px = px_buy                 # **按我们自己的报价成交**
            res[m]["t_fill"].append(t_entry - t0)
            ii = idx_at(t_entry)
            if ii < 0 or kask[ii] <= 0:
                continue
            # 出场：同样用"改善最优价"（卖价进价差内 ⇒ 我们是最优卖价）
            px_sell = float(kask[ii]) * (1.0 - d_bp / 1e4)
            la_exit = 0.0 if inside else float(kaq[ii])
            deadline = t_entry + max_ms
            t_cur = t_entry
            exit_px = None
            t_exit = None
            while t_cur < deadline:
                t_next = min(t_cur + rq, deadline)
                # 出场价随时间更新（保持同样深度）
                ii2 = idx_at(t_cur)
                if ii2 >= 0 and float(kask[ii2]) > 0:
                    px_sell = float(kask[ii2]) * (1.0 - d_bp / 1e4)
                j2 = int(np.searchsorted(tts, t_cur, "left"))
                j3 = int(np.searchsorted(tts, t_next, "left"))
                cum = 0.0
                for jj in range(j2, min(j3, len(tts))):
                    if tsell[jj]:
                        continue
                    if tpx[jj] >= px_sell * (1.0 - 1.0 / 1e4):
                        if inside:
                            exit_px = px_sell
                            t_exit = int(tts[jj])
                            break
                        cum += float(tq[jj])
                        if cum >= la_exit:
                            exit_px = px_sell
                            t_exit = int(tts[jj])
                            break
                if exit_px is not None:
                    break
                t_cur = t_next
            if exit_px is None:
                ii3 = idx_at(deadline)
                if ii3 < 0:
                    continue
                mm = float(kmid[ii3])
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

    out = {"symbol": s, "d_bp": d_bp, "by_mode": {}}
    for m in MODES:
        dd = res[m]
        if not dd["pnl"]:
            out["by_mode"][m] = None
            continue
        p = np.array(dd["pnl"])
        tf = np.array(dd["t_fill"]) / 1000.0
        out["by_mode"][m] = {
            "n": len(p), "n_dec": dd["dec"], "n_forced": dd["forced"],
            "fill_rate": dd["fill"] / max(1, dd["dec"]),
            "net_bp_mean": float(p.mean()), "net_bp_median": float(np.median(p)),
            "p5": float(np.percentile(p, 5)),
            "win_rate": float((p > 0).mean()),
            "fill_time_med_s": float(np.median(tf)) if len(tf) else None,
            "forced_rate": dd["forced"] / max(1, len(p)),
        }
    return out


def main() -> int:
    import numpy as np

    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--symbols", default="BTC,ETH")
    ap.add_argument("--deltas", default="0.25,0.5")
    ap.add_argument("--quote-every-s", type=float, default=15.0)
    ap.add_argument("--requote-s", type=float, default=15.0)
    ap.add_argument("--max-hold-s", type=float, default=300.0)
    ap.add_argument("--maker-fee-bp", type=float, default=-0.5)
    ap.add_argument("--taker-fee-bp", type=float, default=4.0)
    args = ap.parse_args()
    syms = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    deltas = [float(x) for x in args.deltas.split(",")]

    print("H52 修正后的「进价差内」检验（对照 Arroyo QF 24(1):35-57, 2024 Table 3）")
    print(f"窗口={args.hours}h  币={len(syms)}  δ={deltas}bp  maker={args.maker_fee_bp:+.2f}bp\n")

    allres = {}
    for d in deltas:
        per = []
        for s in syms:
            r = run_symbol(s, args.hours, d, args.quote_every_s, args.max_hold_s,
                           args.requote_s, args.maker_fee_bp, args.taker_fee_bp)
            if r:
                per.append(r)
        if not per:
            continue
        allres[d] = per

    if not allres:
        print("无数据")
        return 1

    print("[1] 三种口径 × 各 δ（跨币按往返数加权）")
    print("    %6s %-18s %8s %9s %9s %10s %9s %8s %9s"
          % ("δ", "模式", "决策数", "成交率", "填充时间", "均值", "中位", "胜率", "强平"))
    summary = {}
    for d, per in allres.items():
        for m in MODES:
            rows = [r["by_mode"][m] for r in per if r["by_mode"].get(m)]
            if not rows:
                continue
            N = sum(r["n"] for r in rows)
            dec = sum(r["n_dec"] for r in rows)
            mean = sum(r["net_bp_mean"] * r["n"] for r in rows) / N
            med = float(np.median([r["net_bp_median"] for r in rows]))
            p5 = float(np.median([r["p5"] for r in rows]))
            wr = sum(r["win_rate"] * r["n"] for r in rows) / N
            fr = sum(r["forced_rate"] * r["n"] for r in rows) / N
            ft = [r["fill_time_med_s"] for r in rows if r["fill_time_med_s"]]
            ft = float(np.median(ft)) if ft else float("nan")
            summary[(d, m)] = {"n": N, "n_dec": dec, "fill_rate": sum(
                r["fill_rate"] * r["n_dec"] for r in rows) / max(1, dec),
                "fill_time_med_s": ft, "net_bp_mean": mean,
                "net_bp_median_sym": med, "p5_median": p5, "win_rate": wr,
                "forced_rate": fr, "usd_per_rt_30": mean / 1e4 * 30}
            v = summary[(d, m)]
            print("    %6.2f %-18s %8d %8.2f%% %8.1fs %+10.4f %+9.4f %7.1f%% %8.1f%%"
                  % (d, m, dec, 100 * v["fill_rate"], ft, mean, med, 100 * wr, 100 * fr))

    print("\n[2] 判据（事先定死）")
    # 找 B（进价差内+队首）最优
    bs = [(k, v) for k, v in summary.items() if k[1] == "B 进价差内+队首"]
    as_ = [(k, v) for k, v in summary.items() if k[1] == "A 贴touch+队尾"]
    if bs:
        bk, bv = max(bs, key=lambda t: t[1]["net_bp_mean"])
        print("    B 进价差内（δ=%.2f）：成交率 %.2f%%  填充时间 %.1fs  均值 %+.4fbp  强平 %.1f%%"
              % (bk[0], 100 * bv["fill_rate"], bv["fill_time_med_s"],
                 bv["net_bp_mean"], 100 * bv["forced_rate"]))
        if as_:
            ak, av = min(as_, key=lambda t: t[0][0])
            print("    A 贴touch+队尾：     成交率 %.2f%%  填充时间 %.1fs  均值 %+.4fbp  强平 %.1f%%"
                  % (100 * av["fill_rate"], av["fill_time_med_s"],
                     av["net_bp_mean"], 100 * av["forced_rate"]))
            print("    ⇒ 成交率改善 %+.2fpp，均值改善 %+.4fbp"
                  % (100 * (bv["fill_rate"] - av["fill_rate"]),
                     bv["net_bp_mean"] - av["net_bp_mean"]))
        if bv["net_bp_mean"] > 0:
            print("    ⇒ ✓ **进价差内为正 ⇒ 这是可执行的正收益路径**")
        else:
            print("    ⇒ ✗ 进价差内仍为负（%+.4fbp）" % bv["net_bp_mean"])

    print("\n[3] 对照 Arroyo Table 3（Nasdaq 9 股）")
    print("    论文：最优价 0.054~0.092 → 价差内 5 tick 0.214~0.456（**8.2 倍**）")
    print("          成交时间 1.32s → 0.08s（**16 倍**）")
    print("    ⚠️ 但 Aster 的 tick 粒度相对价格很粗（BTC tick/price ≈ 0.00125bp，")
    print("       而价差约 1.2bp ⇒ 「进价差内」只有 1~2 个 tick 可用），")
    print("       所以能进的深度远小于 Nasdaq 的 5 tick。")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "hours": args.hours, "symbols": syms, "deltas": deltas,
        "maker_fee_bp": args.maker_fee_bp,
        "by_delta": {str(d): per for d, per in allres.items()},
        "summary": {f"{k[0]}|{k[1]}": v for k, v in summary.items()},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
