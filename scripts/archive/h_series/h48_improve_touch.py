"""H48：改善报价换队列位置 —— 能否用"进价差内"绕过队尾约束？

## H47 的致命发现

H47 对照了两种队列口径（BTC/ETH，24h，挂宽 0.5bp，maker −0.5bp）：

    Q0 队首假设（H46 用的）  成交率 96.72%  均值 **+0.5918bp**  强平  3.5%
    Q1 队尾真实             成交率 67.80%  均值 **−2.1635bp**  强平 **54.0%**

**⇒ H46 的 +0.6358bp 建立在"我们排在队首"上，而这对新挂的单不成立：**
真实交易里新单必然排在**队尾**（`LA = 挂单时刻该价位全部挂量`）。

排队尾 ⇒ 吃不到成交 ⇒ 仓位出不去 ⇒ **54% 被 taker 强平** ⇒ 均值转负。

## 但有一条真实机制可以绕过它（论文 §7.2 / Albers 第 1071–1078 行）

> "the order needs to be posted when the queue is still small but becomes large
>  shortly thereafter"

即：**在一个"还没有人排队"的价位挂单** ＝ **改善最优价（进价差内）**。
此时队列里只有我们 ⇒ **我们天然是队首**，LA = 0。

代价：只能在价格穿到我们这一侧时成交（逆向选择更重）。

## 本脚本对照四种组合

    **A 贴 touch + 队尾**（现实：新单排最后）
    **B 进价差内 δ + 队首**（改善最优价 ⇒ 我们是新价位的第一人）
    **C 贴 touch + 队首**（H46 的乐观口径，作为上界）
    **D 进价差内 δ + 队尾**（保守下界，几乎不成交）

扫描 δ ∈ {0.1, 0.25, 0.5, 1.0} bp。

## 判据（事先定死）

  · B 的均值 > 0 ⇒ **"改善最优价"是绕过队尾约束的解** ⇒ 这是可执行的正收益路径
  · B 的均值 < 0 但优于 A ⇒ 方向对，需调 δ
  · B ≈ A ⇒ 改善报价没有换来队列优势，队尾约束无解

用法：
    .venv\\Scripts\\python.exe scripts\\h48_improve_touch.py --hours 24
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

OUT = ROOT / "research_l1" / "out" / "h48_improve_touch.json"


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def run_symbol(s, hours, deltas, quote_every_s, max_hold_s, requote_s,
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
        return int(np.searchsorted(kts, t_ms, "right")) - 1

    step = max(1, int(quote_every_s * 1000 / max(1, int(np.median(np.diff(kts)) or 100))))
    qidx = np.arange(0, len(kts), step, dtype=np.int64)
    max_ms = int(max_hold_s * 1000)
    rq = max(1000, int(requote_s * 1000))

    # 组合：每个 delta 四种口径
    combos = []
    for d in deltas:
        for q in ("touch_tail", "touch_head", "inside_head", "inside_tail"):
            combos.append((d, q))
    res = {k: {"pnl": [], "holds": [], "forced": 0, "fill": 0, "dec": 0} for k in combos}

    for i in qidx:
        raw_bid = float(kbid[i])
        raw_ask = float(kask[i])
        if raw_bid <= 0 or raw_ask <= 0 or raw_ask <= raw_bid:
            continue
        t0 = int(kts[i])
        for d in deltas:
            for q in ("touch_tail", "touch_head", "inside_head", "inside_tail"):
                key = (d, q)
                res[key]["dec"] += 1
                # 报价：inside ⇒ 抬高买价（改善最优价）；touch ⇒ 贴最优买价
                if q.startswith("inside"):
                    px_buy = raw_bid * (1.0 + d / 1e4)
                else:
                    px_buy = raw_bid
                if px_buy >= raw_ask:
                    continue                     # 越过对手价，不是 maker
                # LA：head ⇒ 0（新价位第一人）；tail ⇒ 该价位挂量
                if q.endswith("_head"):
                    la = 0.0
                else:
                    la = float(kbq[i]) if q.startswith("touch") else 0.0
                    # inside 且 tail：该价位原本没人，但我们假设也排最后
                    # ⇒ 用"该时刻最优档挂量"作为保守代理
                    if q == "inside_tail":
                        la = float(kbq[i])
                # 入场
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
                # 出场：同样"改善最优价"⇒ 挂在最优卖价下方 d（更容易成交）
                if q.startswith("inside"):
                    target = float(kask[ii]) * (1.0 - d / 1e4)
                else:
                    target = float(kask[ii])
                if target <= entry_px:
                    continue
                la_exit = 0.0 if q.endswith("_head") else float(kaq[ii])
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
                    res[key]["pnl"].append(net)
                    res[key]["holds"].append(deadline - t_entry)
                    res[key]["forced"] += 1
                else:
                    net = (exit_px - entry_px) / entry_px * 1e4 - 2 * maker_fee_bp
                    res[key]["pnl"].append(net)
                    res[key]["holds"].append(t_exit - t_entry)
                res[key]["fill"] += 1

    out = {"symbol": s, "by_combo": {}}
    for key in combos:
        d = res[key]
        if not d["pnl"]:
            out["by_combo"][f"{key[0]}|{key[1]}"] = None
            continue
        p = np.array(d["pnl"])
        out["by_combo"][f"{key[0]}|{key[1]}"] = {
            "delta_bp": key[0], "mode": key[1],
            "n": len(p), "n_dec": d["dec"], "n_forced": d["forced"],
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
    ap.add_argument("--deltas", default="0,0.25,0.5,1.0")
    ap.add_argument("--quote-every-s", type=float, default=15.0)
    ap.add_argument("--requote-s", type=float, default=15.0)
    ap.add_argument("--max-hold-s", type=float, default=300.0)
    ap.add_argument("--maker-fee-bp", type=float, default=-0.5)
    ap.add_argument("--taker-fee-bp", type=float, default=4.0)
    args = ap.parse_args()
    syms = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    deltas = [float(x) for x in args.deltas.split(",")]

    print("H48 改善报价换队列位置（δ=进价差内的 bp）")
    print(f"窗口={args.hours}h  币={len(syms)}  δ={deltas}  maker={args.maker_fee_bp:+.2f}bp\n")

    per = []
    for s in syms:
        r = run_symbol(s, args.hours, deltas, args.quote_every_s, args.max_hold_s,
                       args.requote_s, args.maker_fee_bp, args.taker_fee_bp)
        if r:
            per.append(r)
    if not per:
        print("无数据")
        return 1

    # 汇总：按 (delta, mode) 聚合
    agg = {}
    for r in per:
        for k, v in r["by_combo"].items():
            if not v:
                continue
            a = agg.setdefault(k, {"n": 0, "dec": 0, "sum": 0.0, "holds": [],
                                   "forced": 0, "mks": [], "p5s": [], "wrs": []})
            a["n"] += v["n"]
            a["dec"] += v["n_dec"]
            a["sum"] += v["net_bp_mean"] * v["n"]
            a["forced"] += v["forced_rate"] * v["n"]
            a["mks"].append(v["net_bp_median"])
            a["p5s"].append(v["p5"])
            a["wrs"].append(v["win_rate"])

    print("[1] 各组合（跨币按往返数加权）")
    print("    %6s %-12s %8s %9s %11s %10s %9s %8s %8s"
          % ("δ bp", "模式", "决策数", "成交率", "均值", "中位", "p5", "胜率", "强平率"))
    rows = []
    for k, a in agg.items():
        d, m = k.split("|")
        mean = a["sum"] / max(1, a["n"])
        rows.append((float(d), m, a, mean))
    for d, m, a, mean in sorted(rows, key=lambda t: (t[0], t[1])):
        print("    %6.2f %-12s %8d %8.2f%% %+11.4f %+10.4f %+9.3f %7.1f%% %7.1f%%"
              % (d, m, a["dec"], 100 * a["n"] / max(1, a["dec"]), mean,
                 float(np.median(a["mks"])), float(np.median(a["p5s"])),
                 100 * float(np.mean(a["wrs"])), 100 * a["forced"] / max(1, a["n"])))

    print("\n[2] 判据（事先定死）")
    # 找 inside_head 中均值最大的
    ih = [(d, m, a, mean) for d, m, a, mean in rows if m == "inside_head"]
    tt = [(d, m, a, mean) for d, m, a, mean in rows if m == "touch_tail"]
    if ih:
        best = max(ih, key=lambda t: t[3])
        print("    **B 进价差内 + 队首**：最优 δ=%.2f ⇒ 均值 %+.4fbp（成交率 %.1f%%，强平 %.1f%%）"
              % (best[0], best[3], 100 * best[2]["n"] / max(1, best[2]["dec"]),
                 100 * best[2]["forced"] / max(1, best[2]["n"])))
        if best[3] > 0:
            print("    ⇒ ✓ **「改善最优价」是绕过队尾约束的解 ⇒ 可执行的正收益路径**")
        else:
            print("    ⇒ ✗ 进价差内也未能转正（最优 %+.4fbp）" % best[3])
    if tt:
        print("    A 贴 touch + 队尾（现实基准）：%s"
              % "  ".join("δ=%.2f %+.4fbp" % (d, mean) for d, m, a, mean in tt))

    print("\n[3] 四种口径的完整对照（δ=0.25 为例）")
    for d, m, a, mean in sorted(rows, key=lambda t: t[0]):
        if abs(d - 0.25) > 1e-9:
            continue
        print("    %-12s 均值 %+9.4fbp  成交率 %6.2f%%  强平 %5.1f%%  胜率 %5.1f%%"
              % (m, mean, 100 * a["n"] / max(1, a["dec"]),
                 100 * a["forced"] / max(1, a["n"]), 100 * float(np.mean(a["wrs"]))))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "hours": args.hours, "symbols": syms, "deltas": deltas,
        "maker_fee_bp": args.maker_fee_bp, "per_symbol": per,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
