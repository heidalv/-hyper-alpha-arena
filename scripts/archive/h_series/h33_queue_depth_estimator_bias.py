"""H33：量化「前方挂量」两种估计的偏差 —— H31 到底被低估了多少成交率？

## 为什么必须单独做这一步

H31 与 H32 给出**方向相反**的结论：

    H31（前方挂量 = top5/5）       P0 成交率 **3.86%**   净额/笔 −0.1971bp
    H32（前方挂量 = 真实最优档）    P0 成交率 **28.46%**  净额/笔 +0.0856bp

两者不可能都对。先定位差异来源 —— **不改模拟，只比估计量**。

## 已经确认的事实（直接查库）

真实最优档挂量（`asterdex_depth_snapshots` 的 `bids->0->>1`）在主流币上极小：

    BTCUSDT   p25=0.07   p50=0.15   p75=0.88
    ETHUSDT   p25=0.76   p50=2.52   p75=4.16
    SOLUSDT   p25=80.79  p50=434.96 p75=540.87
    DOGEUSDT  p25=3773   p50=117887 p75=559590
    XRPUSDT   p25=505.90 p50=7849   p75=24457

而 `top5/5` 给出的中位数是：BTC **38445**、ETH **15553**。

**⇒ 对 BTC，`top5/5` 把前方挂量高估了约 25 万倍。**
   原因：`top5` 是「5 档合计的**名义额**」，而 `bids->0->>1` 是「最优档的**基础币数量**」——
   **两个量纲不同**（一个是 USD，一个是币），除以 5 在数学上就不成立。

## 本脚本做什么

对每个币、每个 15s 桶，用**同一个深度快照**同时取出：
  · `la_real`  = `bids->0->>1`（最优档基础币数量）
  · `top5`     = `bid_depth_top5`（5 档合计名义额）
并计算两者的**比值分布**，从而直接得出：
  · H31 用 `top5/5` 替代 `la_real` 时，偏差是多少倍
  · 这个偏差在哪些币上致命、在哪些币上无害

## 判据

  · 比值 `top5/5 / la_real` ≫ 1 ⇒ H31 **高估前方挂量** ⇒ 成交率被**低估** ⇒ H31 偏悲观
  · 比值 ≈ 1 ⇒ 两种估计等价 ⇒ H31/H32 的差异另有原因
  · 比值 ≪ 1 ⇒ H31 低估前方挂量 ⇒ 成交率被高估

用法：
    .venv\\Scripts\\python.exe scripts\\h33_queue_depth_estimator_bias.py --hours 72
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
    ap.add_argument("--tol-ms", type=int, default=4000,
                    help="深度快照与桶边界（桶起点+15s）的最大时差")
    args = ap.parse_args()

    import numpy as np
    import psycopg2
    import psycopg2.extras

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    since = int(args.hours * 3600_000)

    print("H33 前方挂量估计偏差量化")
    print(f"窗口={args.hours}h  币={len(syms)}  深度匹配容差=±{args.tol_ms}ms\n")

    rows_out = []
    for s in syms:
        vs = s if s.endswith("USDT") else f"{s}USDT"
        cur.execute(
            "SELECT timestamp, bid_depth_top5, ask_depth_top5"
            "  FROM market_trades_aggregated"
            " WHERE exchange='asterdex' AND symbol=%s"
            "   AND timestamp > (extract(epoch from now())*1000)::bigint - %s"
            " ORDER BY timestamp",
            (s, since),
        )
        buckets = cur.fetchall()
        cur.execute(
            "SELECT event_ts_ms, (bids->0->>1)::float AS bq, (asks->0->>1)::float AS aq,"
            "       (bids->0->>0)::float AS bp, (asks->0->>0)::float AS ap"
            "  FROM asterdex_depth_snapshots"
            " WHERE symbol=%s AND event_ts_ms > (extract(epoch from now())*1000)::bigint - %s"
            " ORDER BY event_ts_ms",
            (vs, since),
        )
        dr = cur.fetchall()
        if len(buckets) < 300 or len(dr) < 100:
            print(f"  {s:<8} 数据不足（桶 {len(buckets)} / 深度 {len(dr)}）→ 跳过")
            continue

        bts = np.array([int(b["timestamp"]) for b in buckets], dtype=np.int64)
        top5 = np.array([float(b["bid_depth_top5"] or 0) for b in buckets])
        dts = np.array([int(x["event_ts_ms"]) for x in dr], dtype=np.int64)
        dbq = np.array([float(x["bq"] or 0) for x in dr])
        dbp = np.array([float(x["bp"] or 0) for x in dr])

        # 桶覆盖 [ts, ts+15000)。取桶内最后一个深度快照（最接近桶末）。
        end_ts = bts + 15000
        idx = np.searchsorted(dts, end_ts, side="right") - 1
        idx = np.clip(idx, 0, len(dts) - 1)
        lag = end_ts - dts[idx]            # >=0 表示快照在桶末之前
        ok = (lag >= 0) & (lag <= args.tol_ms)
        # 只保留真正落在桶内的
        n_ok = int(ok.sum())
        if n_ok < 100:
            print(f"  {s:<8} 桶内能匹配到深度快照的只有 {n_ok}/{len(buckets)} → 跳过")
            continue

        la_real = dbq[idx][ok]                       # 最优档挂量（基础币数量）
        top5_est = top5[ok] / 5.0                    # H31 的估计（USD 口径 / 5）
        px = dbp[idx][ok]                            # 最优买价
        la_real_usd = la_real * px                   # 折算成 USD，才能与 top5 比

        with np.errstate(divide="ignore", invalid="ignore"):
            ratio_usd = np.where(la_real_usd > 0, top5_est / la_real_usd, np.nan)
            ratio_raw = np.where(la_real > 0, top5_est / la_real, np.nan)

        def _q(a, p):
            a = a[np.isfinite(a)]
            return float(np.percentile(a, p)) if len(a) else float("nan")

        rows_out.append({
            "symbol": s, "n_matched": n_ok, "n_buckets": len(buckets),
            "la_real_med": _q(la_real, 50),
            "la_real_usd_med": _q(la_real_usd, 50),
            "top5_over5_med": _q(top5_est, 50),
            "ratio_usd_p25": _q(ratio_usd, 25),
            "ratio_usd_med": _q(ratio_usd, 50),
            "ratio_usd_p75": _q(ratio_usd, 75),
            "ratio_raw_med": _q(ratio_raw, 50),
        })
        print("  %-8s 匹配 %6d/%-6d  最优档 %12.2f 币 = $%12.0f   top5/5 $%12.0f"
              % (s, n_ok, len(buckets), _q(la_real, 50), _q(la_real_usd, 50),
                 _q(top5_est, 50)))
        print("           **同量纲比值 top5/5 ÷ 最优档(USD): "
              "p25 %.2f  中位 %.2f  p75 %.2f**"
              % (_q(ratio_usd, 25), _q(ratio_usd, 50), _q(ratio_usd, 75)))

    if not rows_out:
        print("\n无可用数据")
        return 1

    print("\n[汇总] 同量纲比值（top5/5 ÷ 最优档USD）")
    print("    %-8s %14s %14s %10s" % ("symbol", "p25", "中位", "结论"))
    over = under = eq = 0
    for r in rows_out:
        m = r["ratio_usd_med"]
        if not np.isfinite(m):
            lab = "无数据"
        elif m > 2:
            lab = "H31 高估挂量 ⇒ 成交率被低估"; over += 1
        elif m < 0.5:
            lab = "H31 低估挂量 ⇒ 成交率被高估"; under += 1
        else:
            lab = "两者相当"; eq += 1
        print("    %-8s %14.2f %14.2f %10s" % (r["symbol"], r["ratio_usd_p25"], m, lab))

    print("\n[判定]")
    if over > under:
        print("    ⇒ 多数币上 H31 **高估**了前方挂量 ⇒ 它的成交率 3.86% 是**下界**，")
        print("      H32 的 28.46% 更接近真相。**H31 的悲观结论需要修正。**")
    elif under > over:
        print("    ⇒ 多数币上 H31 低估了前方挂量 ⇒ 它的成交率被高估。")
    else:
        print("    ⇒ 两种估计大致相当 ⇒ H31/H32 的差异另有原因（需查模拟逻辑）。")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / "h33_queue_depth_estimator_bias.json"
    p.write_text(json.dumps({"hours": args.hours, "rows": rows_out},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {p}")
    cur.close()
    cn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
