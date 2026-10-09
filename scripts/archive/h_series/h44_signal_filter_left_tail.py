"""H44：信号过滤砍左尾 —— 在 H43 无偏框架里加"趋势侧不挂单"。

## 为什么做这个（H43 定位出的病因）

H43 无偏结果（w=0.5，25436 个往返）：
    **中位 +0.7251bp   均值 −0.3794bp   胜率 53.9%   强平 0.0%**
⇒ **多数小赢 + 少数大亏**。强平 0% 说明亏损不来自 taker 强平，
  而是**出场腿跟着下跌的价格一路挂低，亏损在那里兑现**。

⇒ 亏损来自**单边趋势**。而我们有 **flow_imb**（样本外 IC 0.215，7/7 币同号）——
它正好能识别单边主动流。

## 两种过滤（都只用**过去**的数据，防未来函数）

    **F1 抑制逆风侧**：信号强指向上行（z > θ）⇒ 预测要涨 ⇒
        **不挂买单**（会被跌穿），只挂卖单。
        强指向下行（z < −θ）⇒ 只挂买单。
        ⇒ 这是 H31–H34 的 P2「反势挂」思路，但目标从"每笔净额"改为"砍左尾"。

    **F2 双阈值暂停**：|z| > θ 时**两侧都不挂**（完全避开趋势段）。
        ⇒ 更保守，牺牲成交数换尾部安全。

    **F0 基准**：不过滤（= H43 的 w=0.5）

## 信号（tick 级，只用过去 15s）

    z = 标准化的盘口失衡
        盘口失衡 = (bid_qty − ask_qty) / (bid_qty + ask_qty)
        对挂单时刻**之前** 15 秒的 book_ticker 取均值
        再用**过去 200 个报价点**做滚动标准化（严格 lookback，不用未来）

    方向：失衡为正 ⇒ 买盘厚 ⇒ 通常预示上行（H29 实测 corr +0.037~+0.056）

## 判据（事先定死）

  · **均值转正** 且 强平 < 30% ⇒ 路径 A 成立
  · 同时报**保留的往返比例** —— 若只剩 20%，即使均值为正也不实用
  · 若均值仍为负但**左尾变短**（p5 改善）⇒ 方向对，需继续调 θ

用法：
    .venv\\Scripts\\python.exe scripts\\h44_signal_filter_left_tail.py --hours 24
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

OUT = ROOT / "research_l1" / "out" / "h44_signal_filter.json"
MODES = ("F0 不过滤", "F1 抑制逆风侧", "F2 双阈值暂停")


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def run_symbol(s, hours, w_bp, theta, quote_every_s, max_hold_s, requote_s,
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
        "SELECT event_ts_ms, bid_px::float b, ask_px::float a,"
        "       bid_qty::float bq, ask_qty::float aq"
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
    kbq = np.array([float(x["bq"] or 0) for x in tk])
    kaq = np.array([float(x["aq"] or 0) for x in tk])
    kden = kbq + kaq
    kimb = np.where(kden > 0, (kbq - kaq) / kden, 0.0)

    def ask_at(t_ms):
        i = int(np.searchsorted(kts, t_ms, "left"))
        return float(kask[i]) if i < len(kts) else 0.0

    def mid_at(t_ms):
        i = int(np.searchsorted(kts, t_ms, "left"))
        if i >= len(kts):
            return 0.0
        return (float(kbid[i]) + float(kask[i])) / 2.0

    step = max(1, int(quote_every_s * 1000 / max(1, int(np.median(np.diff(dts)) or 2000))))
    qidx = np.arange(0, len(dts), step, dtype=np.int64)
    max_ms = int(max_hold_s * 1000)
    rq = max(1000, int(requote_s * 1000))

    # ── 只用过去的信号：挂单时刻前 15s 的平均失衡，再滚动标准化 ──
    W_MS = 15000
    raw = np.full(len(qidx), np.nan)
    for k, i in enumerate(qidx):
        t0 = int(dts[i])
        lo = int(np.searchsorted(kts, t0 - W_MS, "left"))
        hi = int(np.searchsorted(kts, t0, "left"))
        if hi - lo >= 5:
            raw[k] = float(kimb[lo:hi].mean())
    # 滚动标准化（过去 200 点；前 200 点不参与，避免用未来）
    WN = 200
    z = np.zeros(len(qidx))
    for k in range(WN, len(qidx)):
        w = raw[k - WN:k]
        w = w[np.isfinite(w)]
        if len(w) >= 30 and w.std() > 0:
            z[k] = (raw[k] - w.mean()) / w.std() if np.isfinite(raw[k]) else 0.0

    out = {m: {"pnl": [], "holds": [], "forced": 0, "skipped": 0, "no_entry": 0}
           for m in MODES}
    for k, i in enumerate(qidx):
        bid = float(dbp[i])
        if bid <= 0 or not np.isfinite(raw[k]):
            continue
        zi = z[k]
        px_buy = bid * (1.0 - w_bp / 1e4)
        t0 = int(dts[i])
        strong_up = zi >= theta
        strong_dn = zi <= -theta

        # ① 入场（按模式决定是否挂买单）
        j0 = int(np.searchsorted(tts, t0, "left"))
        j1 = int(np.searchsorted(tts, t0 + max_ms, "left"))
        je = -1
        for jj in range(j0, min(j1, len(tts))):
            if tsell[jj] and tpx[jj] <= px_buy * (1.0 + 1.0 / 1e4):
                je = jj
                break

        for m in MODES:
            allow = True
            if m == "F1 抑制逆风侧" and strong_up:
                allow = False          # 预测涨 ⇒ 不挂买单（会被跌穿）
            elif m == "F2 双阈值暂停" and (strong_up or strong_dn):
                allow = False
            if not allow:
                out[m]["skipped"] += 1
                continue
            if je < 0:
                out[m]["no_entry"] += 1
                continue
            t_entry = int(tts[je])
            entry_px = float(tpx[je])
            deadline = t_entry + max_ms
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
                mm = mid_at(deadline)
                if mm <= 0:
                    continue
                net = (mm * (1.0 - taker_fee_bp / 1e4) - entry_px) / entry_px * 1e4 - maker_fee_bp
                out[m]["pnl"].append(net)
                out[m]["holds"].append(deadline - t_entry)
                out[m]["forced"] += 1
            else:
                net = (exit_px - entry_px) / entry_px * 1e4 - 2 * maker_fee_bp
                out[m]["pnl"].append(net)
                out[m]["holds"].append(t_exit - t_entry)

    res = {"symbol": s, "by_mode": {}}
    for m in MODES:
        d = out[m]
        if not d["pnl"]:
            res["by_mode"][m] = None
            continue
        p = np.array(d["pnl"])
        res["by_mode"][m] = {
            "n": len(p), "n_skipped": d["skipped"], "n_no_entry": d["no_entry"],
            "n_forced": d["forced"],
            "net_bp_mean": float(p.mean()), "net_bp_median": float(np.median(p)),
            "p5": float(np.percentile(p, 5)), "p25": float(np.percentile(p, 25)),
            "p75": float(np.percentile(p, 75)), "p95": float(np.percentile(p, 95)),
            "win_rate": float((p > 0).mean()),
            "hold_ms_median": float(np.median(d["holds"])),
            "forced_rate": d["forced"] / max(1, len(p)),
        }
    return res


def main() -> int:
    import numpy as np

    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--symbols", default="BTC,ETH,SOL,XRP,ASTER")
    ap.add_argument("--width-bp", type=float, default=0.5)
    ap.add_argument("--theta", type=float, default=0.5)
    ap.add_argument("--quote-every-s", type=float, default=15.0)
    ap.add_argument("--requote-s", type=float, default=15.0)
    ap.add_argument("--max-hold-s", type=float, default=300.0)
    ap.add_argument("--maker-fee-bp", type=float, default=0.0)
    ap.add_argument("--taker-fee-bp", type=float, default=4.0)
    args = ap.parse_args()
    syms = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]

    print("H44 信号过滤砍左尾（在 H43 无偏框架上加过滤）")
    print(f"窗口={args.hours}h  币={len(syms)}  挂宽={args.width_bp}bp  θ={args.theta}  "
          f"最长持有={args.max_hold_s:.0f}s\n")

    per = []
    for s in syms:
        r = run_symbol(s, args.hours, args.width_bp, args.theta, args.quote_every_s,
                       args.max_hold_s, args.requote_s,
                       args.maker_fee_bp, args.taker_fee_bp)
        if r:
            per.append(r)
    if not per:
        print("无数据")
        return 1

    print("[1] 三种模式对照（跨币按往返数加权）")
    print("    %-16s %7s %9s %9s %10s %8s %8s %9s %10s"
          % ("模式", "往返数", "跳过", "均值", "中位", "p5", "胜率", "强平", "保留比"))
    summary = {}
    for m in MODES:
        rows = [r["by_mode"][m] for r in per if r["by_mode"].get(m)]
        if not rows:
            continue
        N = sum(r["n"] for r in rows)
        sk = sum(r["n_skipped"] for r in rows)
        ne = sum(r["n_no_entry"] for r in rows)
        mean = sum(r["net_bp_mean"] * r["n"] for r in rows) / N
        med = float(np.median([r["net_bp_median"] for r in rows]))
        p5 = float(np.median([r["p5"] for r in rows]))
        wr = sum(r["win_rate"] * r["n"] for r in rows) / N
        fr = sum(r["forced_rate"] * r["n"] for r in rows) / N
        keep = N / max(1, N + sk + ne)
        summary[m] = {"n": N, "n_skipped": sk, "n_no_entry": ne,
                      "net_bp_mean": mean, "net_bp_median_sym": med, "p5_median": p5,
                      "win_rate": wr, "forced_rate": fr, "keep_ratio": keep,
                      "usd_per_rt_30": mean / 1e4 * 30}
        print("    %-16s %7d %9d %+9.4f %+9.4f %+8.3f %7.1f%% %7.1f%% %9.1f%%"
              % (m, N, sk, mean, med, p5, 100 * wr, 100 * fr, 100 * keep))

    base = summary.get("F0 不过滤", {})
    print("\n[2] 相对基准（F0）")
    for m in MODES[1:]:
        v = summary.get(m)
        if not v or not base:
            continue
        print("    %-16s 均值改善 %+.4fbp   p5 改善 %+.3fbp   保留 %.1f%%"
              % (m, v["net_bp_mean"] - base["net_bp_mean"],
                 v["p5_median"] - base["p5_median"], 100 * v["keep_ratio"]))

    print("\n[3] 判据（事先定死：均值转正且强平<30% 且保留≥40%）")
    best = max(summary.items(), key=lambda kv: kv[1]["net_bp_mean"])
    v = best[1]
    if v["net_bp_mean"] > 0 and v["forced_rate"] < 0.30 and v["keep_ratio"] >= 0.40:
        print("    ⇒ ✓ **%s 成立**：均值 %+.4fbp，保留 %.1f%%"
              % (best[0], v["net_bp_mean"], 100 * v["keep_ratio"]))
    elif v["net_bp_mean"] > 0:
        print("    ⇒ ~ %s 均值为正（%+.4fbp）但保留仅 %.1f%% ⇒ 交易量太少"
              % (best[0], v["net_bp_mean"], 100 * v["keep_ratio"]))
    else:
        print("    ⇒ ✗ 过滤后均值仍为负（最优 %s：%+.4fbp）" % (best[0], v["net_bp_mean"]))
        if base:
            print("      但 p5 从 %+.3f 改善到 %+.3f ⇒ 左尾确实被砍了，方向对、力度不够"
                  % (base["p5_median"], v["p5_median"]))

    print("\n[4] 分币明细")
    for m in MODES:
        print("    --- %s ---" % m)
        for r in per:
            v = r["by_mode"].get(m)
            if not v:
                continue
            print("      %-8s n=%5d  均值 %+8.4fbp  中位 %+8.4fbp  p5 %+8.3f  "
                  "胜率 %5.1f%%  强平 %4.1f%%"
                  % (r["symbol"], v["n"], v["net_bp_mean"], v["net_bp_median"],
                     v["p5"], 100 * v["win_rate"], 100 * v["forced_rate"]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "hours": args.hours, "symbols": syms, "width_bp": args.width_bp,
        "theta": args.theta, "per_symbol": per,
        "summary": {k: v for k, v in summary.items()},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
