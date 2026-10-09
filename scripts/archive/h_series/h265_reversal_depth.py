# -*- coding: utf-8 -*-
"""H265 验证 4：逆势单的挂深 Δ 敏感性（只挂逆势侧，收益来自反转）。

# 背景

设计（全面升级 v2）P4 = 逆势单最优挂深。做市下 spread_mult=0.5 → 挂深 0.28bp。
反转交易是**只挂逆势单**（涨了挂卖、跌了挂买），挂深 Δ 是新的变量：

    涨了（trend>0）→ 挂卖在 mid + Δ（等价格继续涨到我们卖价，反转回落）
    跌了（trend<0）→ 挂买在 mid − Δ（等价格继续跌到我们买价，反转反弹）

Δ 越大：成交价越有利（卖得更高/买得更低），但成交率越低（价格要走更远）
Δ 越小：成交率越高，但成交价接近 mid

# 本脚本（纯 tick，与 H263/H264 同口径）

对每个 Δ ∈ {0.2, 0.5, 1, 2, 4, 8}bp：
  1. lookback=120s 判趋势，逆势侧挂单在 mid ± Δ
  2. 未来 60s 内是否被触及（成交）
  3. 成交后逆势漂移 = −sign(trend) × (未来价格 − 成交价)
  4. 净额 = 成交率 × E[逆势漂移 | 成交]

# 判据

  · 净额（每 tick 期望）随 Δ 的变化：峰值处 = 最优挂深
  · 与当前做市挂深（0.28bp）对比，看反转单该挂多深

# 用法

    python scripts/h265_reversal_depth.py --hours 12
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h265_reversal_depth.json"
SYMS = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT"]
K = 120.0
M = 60.0
STEP = 30.0
DELTAS = [0.2, 0.5, 1.0, 2.0, 4.0, 8.0]


def market_dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    base = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        base = base.replace(j, "")
    return base.rsplit("/", 1)[0] + "/alpha_market"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=12.0)
    a = ap.parse_args()

    import psycopg
    print("=" * 104)
    print(f"H265  逆势单挂深敏感性（lookback={K:g}s → 持有 {M:g}s）")
    print("=" * 104)
    print(f"\n  窗口 {a.hours:g}h")

    # 逐币 tick
    series = {}
    for sym in SYMS:
        try:
            with psycopg.connect(market_dsn()) as c:
                with c.cursor() as cur:
                    cur.execute("""
                        SELECT DISTINCT ON (bucket) bucket, mid
                        FROM (SELECT (event_ts_ms/1000) AS bucket,
                                     (bid_px+ask_px)/2.0 AS mid
                              FROM asterdex_book_ticker
                              WHERE ingest_ts >= now() - (%s || ' hours')::interval
                                AND symbol = %s AND bid_px>0 AND ask_px>bid_px) t
                        ORDER BY bucket, mid
                    """, (str(float(a.hours) + 0.3), sym))
                    recs = cur.fetchall()
        except Exception as e:
            print(f"  ⚠️ {sym}: {str(e)[:50]}")
            continue
        d = {int(b): float(mid) for b, mid in recs}
        ks = sorted(d)
        series[sym.replace("USDT", "")] = (ks, [d[k] for k in ks])

    # 每个 Δ：成交率 + 逆势漂移期望
    print(f"\n  {'Δ(bp)':>8}{'成交率':>9}{'逆势漂移bp':>13}{'**每tick期望bp**':>16}")
    for D in DELTAS:
        hits, tot = 0, 0
        drifts = []
        for sym, (ks, mids) in series.items():
            n = len(ks)
            last = -1e18
            for i in range(n):
                if ks[i] - last < STEP:
                    continue
                # 趋势
                j = i
                while j >= 0 and ks[i] - ks[j] < K:
                    j -= 1
                if j < 0 or ks[i] - ks[j] < K * 0.9 or mids[j] <= 0:
                    continue
                trend = (mids[i] - mids[j]) / mids[j] * 1e4
                last = ks[i]
                tot += 1
                # 逆势挂单：涨了挂卖 mid+Δ、跌了挂买 mid−Δ
                if trend > 0:
                    entry = mids[i] * (1.0 + D / 1e4)   # 卖价
                    side = "sell"
                else:
                    entry = mids[i] * (1.0 - D / 1e4)   # 买价
                    side = "buy"
                # 未来 M 秒内是否触及
                f = i + 1
                hit = False
                t_hit = i
                while f < n and ks[f] - ks[i] <= M:
                    if side == "sell" and mids[f] >= entry:
                        hit = True
                        t_hit = f
                        break
                    if side == "buy" and mids[f] <= entry:
                        hit = True
                        t_hit = f
                        break
                    f += 1
                if not hit:
                    continue
                hits += 1
                # 逆势漂移：成交后到 M 秒末，对我们有利的移动
                f2 = t_hit
                while f2 + 1 < n and ks[f2 + 1] - ks[i] <= M:
                    f2 += 1
                if f2 <= t_hit:
                    continue
                end_price = mids[f2]
                if side == "sell":      # 做空，有利 = 价格回落
                    drift = (entry - end_price) / entry * 1e4
                else:                    # 做多，有利 = 价格反弹
                    drift = (end_price - entry) / entry * 1e4
                drifts.append(drift)
        rate = hits / tot if tot else 0.0
        drift_mean = st.mean(drifts) if drifts else 0.0
        exp = rate * drift_mean
        print(f"  {D:>8.1f}{rate*100:>8.2f}%{drift_mean:>+13.3f}{exp:>+16.3f}")

    print(f"\n{'━'*104}\n  结论\n{'━'*104}")
    print(f"\n  「每 tick 期望 = 成交率 × 逆势漂移」随 Δ 的变化，峰值处 = 最优挂深")
    print(f"  当前做市挂深 ≈ 0.28bp（spread_mult=0.5 × 半价差 0.56bp）")
    print(f"  ⚠️ 无队列模型，Δ 大时成交率被高估；这是上界估计")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "k": K, "m": M, "deltas": DELTAS},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
