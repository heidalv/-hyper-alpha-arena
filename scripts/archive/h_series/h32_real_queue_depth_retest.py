"""H32：用**真实逐档挂量**重跑择时挂单 —— 成交率是硬约束还是我的估计太保守？

## 动机（H31 留下的最大疑点）

H31 实测（106,352 次决策，7 币）：
    P0 两侧都挂   成交率 3.86%   净额 −0.1971bp/笔
    P2 反势挂     成交率 2.55%   净额 **+0.0518bp/笔**   ← 唯一为正
但三种用法对"每决策净额"的改善都 < 0.01bp，按判据不显著。

**关键疑点**：H31 的前方挂量用的是
    `market_trades_aggregated.bid_depth_top5 / 5`
—— 拿 **5 档合计** 除以 5 当作"最优档挂量"。
这在**深度分布不均**时严重高估（若最优档很薄、第 2–5 档很厚，除以 5 会远大于真实最优档）。
前方挂量估得越大 ⇒ 越难达到成交阈值 ⇒ **成交率被系统性低估**。

## 本脚本的修正

改用 `asterdex_depth_snapshots` 的**真实最优档挂量**：
    `bids->0->>1`（最优买价上的挂量）
    `asks->0->>1`（最优卖价上的挂量）
这正是论文口径里的 LA（liquidity ahead）。

## 对成交率的影响方向（事先声明，便于事后核对）

前方挂量↓ ⇒ 同样的对手方主动量更容易吃穿 ⇒ **成交率↑**。
若成交率从 3.86% 升到 >20%，则 H31 的"每决策价值"应随之放大约 5 倍；
若仍 <5%，则**成交率是硬约束**，与估计方式无关。

## 判据（事先定死）

  · 成交率 > 20% ⇒ 估计方式曾是主要瓶颈，重估全部结论
  · 成交率 5~20% ⇒ 有改善但不足以改变结论（每决策价值仍 <0.05bp）
  · 成交率 < 5%  ⇒ **成交率是硬约束**，H31 的结论成立

用法：
    .venv\\Scripts\\python.exe scripts\\h32_real_queue_depth_retest.py --hours 72
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

OUT_DIR = ROOT / "research_l1" / "out"


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=72.0)
    ap.add_argument("--symbols", default="BTC,ETH,BNB,SOL,DOGE,XRP,ASTER")
    ap.add_argument("--theta", type=float, default=0.5)
    ap.add_argument("--leg-usd", type=float, default=30.0)
    ap.add_argument("--depth-tol-ms", type=int, default=30000,
                    help="深度快照与桶时刻的最大匹配间隔")
    args = ap.parse_args()

    import numpy as np
    import psycopg2
    import psycopg2.extras

    from h29_feature_ic_scan import build_features  # noqa: E402

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    since = int(args.hours * 3600_000)

    print("H32 真实逐档挂量重测（对照 H31 的 top5/5 估计）")
    print(f"窗口={args.hours}h  币={len(syms)}  θ={args.theta}  "
          f"深度匹配容差={args.depth_tol_ms}ms\n")


# [F259 修正] h32_real_queue_depth_retest.py 的 markout 公式（见 _fix_abs_markout.py）
# net = half + mk（mk 自带方向）
# 旧写法 half - abs(mk) 把 markout 的标准差当成成本，
# 实测 30s 上虚增 1.47bp/笔（BTC 2h 样本）。
    agg = {}
    diag = {}
    for s in syms:
        vs = s if s.endswith("USDT") else f"{s}USDT"
        cur.execute(
            "SELECT timestamp, taker_buy_volume, taker_sell_volume,"
            "       taker_buy_count, taker_sell_count,"
            "       taker_buy_notional, taker_sell_notional,"
            "       vwap, high_price, low_price,"
            "       bid_depth_top5, ask_depth_top5, largest_trade_usd"
            "  FROM market_trades_aggregated"
            " WHERE exchange = 'asterdex' AND symbol = %s"
            "   AND timestamp > (extract(epoch from now())*1000)::bigint - %s"
            " ORDER BY timestamp",
            (s, since),
        )
        rows = cur.fetchall()
        # 真实最优档挂量
        cur.execute(
            "SELECT event_ts_ms, (bids->0->>0)::float AS bp, (bids->0->>1)::float AS bq,"
            "       (asks->0->>0)::float AS ap, (asks->0->>1)::float AS aq"
            "  FROM asterdex_depth_snapshots"
            " WHERE symbol = %s AND event_ts_ms > (extract(epoch from now())*1000)::bigint - %s"
            " ORDER BY event_ts_ms",
            (vs, since),
        )
        dr = cur.fetchall()
        if len(rows) < 800 or len(dr) < 200:
            print(f"  {s:<8} 数据不足（桶 {len(rows)} / 深度 {len(dr)}）→ 跳过")
            continue

        d = build_features(rows)
        if not d:
            continue
        n = d["n"]
        F = d["F"]
        ts = np.array([int(r["timestamp"]) for r in rows], dtype=np.int64)
        hi = np.array([float(r["high_price"] or 0) for r in rows])
        lo = np.array([float(r["low_price"] or 0) for r in rows])
        tsv = np.array([float(r["taker_sell_volume"] or 0) for r in rows])
        tbv = np.array([float(r["taker_buy_volume"] or 0) for r in rows])
        bd5 = np.array([float(r["bid_depth_top5"] or 0) for r in rows])
        ad5 = np.array([float(r["ask_depth_top5"] or 0) for r in rows])
        mid = (hi + lo) / 2.0

        dts = np.array([int(x["event_ts_ms"]) for x in dr], dtype=np.int64)
        dbq = np.array([float(x["bq"] or 0) for x in dr])
        daq = np.array([float(x["aq"] or 0) for x in dr])

        # 把每个桶映射到最近的深度快照
        idx = np.searchsorted(dts, ts, side="right") - 1
        idx = np.clip(idx, 0, len(dts) - 1)
        lag = np.abs(ts - dts[idx])
        ok = lag <= args.depth_tol_ms
        # 真实前方挂量（匹配失败的用 top5/5 兜底，并单独计数）
        la_bid = np.where(ok, dbq[idx], bd5 / 5.0)
        la_ask = np.where(ok, daq[idx], ad5 / 5.0)
        matched = int(ok.sum())

        sig = np.nan_to_num(F["flow_imb"], nan=0.0) + np.nan_to_num(F["depth_imb"], nan=0.0)
        W = 200
        mu = np.full(n, np.nan)
        sd = np.full(n, np.nan)
        for i in range(W, n):
            w = sig[i - W:i]
            mu[i] = w.mean()
            sd[i] = w.std() if w.std() > 0 else 1.0
        z = np.where(np.isfinite(sd) & (sd > 0), (sig - mu) / sd, 0.0)

        diag[s] = {"buckets": n, "depth_matched": matched,
                   "la_bid_med": float(np.median(la_bid[ok])) if ok.any() else None,
                   "top5_over5_med": float(np.median(bd5 / 5.0)),
                   "depth_snapshots": len(dr)}

        for pol in ("P0 两侧都挂", "P2 反势挂", "P3 择时"):
            agg.setdefault(f"{s}|{pol}", {"net": [], "n_dec": 0, "n_fill": 0})

        for i in range(W, n - 1):
            zi = z[i]
            if not np.isfinite(zi):
                continue
            bid, ask = lo[i], hi[i]
            if bid <= 0 or ask <= 0 or ask <= bid:
                continue
            q_bid = max(1e-12, la_bid[i])
            q_ask = max(1e-12, la_ask[i])
            sp_half_bid = (mid[i] - bid) / mid[i] * 1e4
            sp_half_ask = (ask - mid[i]) / mid[i] * 1e4
            strong_up = zi >= args.theta
            strong_dn = zi <= -args.theta

            for pol in ("P0 两侧都挂", "P2 反势挂", "P3 择时"):
                do_buy, do_sell = True, True
                if pol == "P2 反势挂":
                    do_buy, do_sell = strong_dn, strong_up
                elif pol == "P3 择时":
                    if strong_up:
                        do_buy, do_sell = False, True
                    elif strong_dn:
                        do_buy, do_sell = True, False
                if not (do_buy or do_sell):
                    continue
                st = agg[f"{s}|{pol}"]
                st["n_dec"] += 1
                got = False
                if do_buy and tsv[i + 1] >= q_bid and lo[i + 1] <= bid:
                    mk = (mid[i + 1] - bid) / bid * 1e4
                    st["net"].append(sp_half_bid + mk)
                    got = True
                if do_sell and tbv[i + 1] >= q_ask and hi[i + 1] >= ask:
                    mk = (ask - mid[i + 1]) / ask * 1e4
                    st["net"].append(sp_half_ask + mk)
                    got = True
                if got:
                    st["n_fill"] += 1
        print(f"  {s:<8} 桶 {n:>6}  深度匹配 {matched:>6}  "
              f"真实最优档挂量中位 {diag[s]['la_bid_med']:.1f} vs top5/5 {diag[s]['top5_over5_med']:.1f}")

    if not agg:
        print("\n无数据")
        return 1

    policies = ["P0 两侧都挂", "P2 反势挂", "P3 择时"]
    print("\n[1] 结果（真实逐档挂量）")
    print("    %-14s %10s %10s %10s %12s %14s"
          % ("策略", "决策数", "成交数", "成交率", "净额bp/笔", "净额bp/决策"))
    summary = {}
    for pol in policies:
        nets, ndec, nfill = [], 0, 0
        for k, v in agg.items():
            if not k.endswith(pol):
                continue
            nets.extend(v["net"])
            ndec += v["n_dec"]
            nfill += v["n_fill"]
        if not nets:
            continue
        a = np.array(nets)
        summary[pol] = {"n_dec": ndec, "n_fill": nfill,
                        "fill_rate": nfill / max(1, ndec),
                        "net_bp_per_fill": float(a.mean()),
                        "net_bp_per_dec": float(a.sum() / max(1, ndec))}
        print("    %-14s %10d %10d %9.2f%% %12.4f %14.4f"
              % (pol, ndec, nfill, 100.0 * nfill / max(1, ndec), a.mean(),
                 a.sum() / max(1, ndec)))

    print("\n[2] 对照 H31（top5/5 估计）")
    print("    H31: P0 成交率 3.86% 净额/笔 −0.1971bp 净额/决策 −0.0082bp")
    print("    H31: P2 成交率 2.55% 净额/笔 +0.0518bp 净额/决策 +0.0013bp")
    fr = summary.get("P0 两侧都挂", {}).get("fill_rate")
    if fr is not None:
        print("    H32: P0 成交率 %.2f%%" % (100 * fr))

    print("\n[3] 判定（事先定死）")
    if fr is None:
        print("    无成交率数据")
    elif fr > 0.20:
        print("    ⇒ 成交率 %.1f%% > 20%% ⇒ **估计方式曾是主要瓶颈**，" % (100 * fr))
        print("      H31 的全部结论需要按新成交率重估。")
    elif fr > 0.05:
        print("    ⇒ 成交率 %.1f%% 落在 5~20%% ⇒ 有改善，但每决策价值仍 <0.05bp，"
              % (100 * fr))
        print("      不足以改变结论。")
    else:
        print("    ⇒ 成交率 %.1f%% < 5%% ⇒ **成交率是硬约束**，与挂量估计方式无关。" % (100 * fr))
        print("      H31 的结论成立：预测力无法通过被动挂单变现。")

    print("\n[4] 挂量口径对照（诊断）")
    for s, dg in sorted(diag.items()):
        if dg["la_bid_med"]:
            r = dg["la_bid_med"] / max(1e-9, dg["top5_over5_med"])
            print("    %-8s 真实最优档 %.0f  top5/5 %.0f  比值 %.2f"
                  % (s, dg["la_bid_med"], dg["top5_over5_med"], r))
    print("    比值 >1 表示 top5/5 低估了挂量（会让成交率被高估）；")
    print("    比值 <1 表示 top5/5 高估了挂量（会让成交率被低估 —— 这是 H31 的情形）。")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / "h32_real_queue_depth_retest.json"
    p.write_text(json.dumps({"hours": args.hours, "symbols": syms, "theta": args.theta,
                             "summary": summary, "diagnostics": diag},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {p}")
    cur.close()
    cn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
