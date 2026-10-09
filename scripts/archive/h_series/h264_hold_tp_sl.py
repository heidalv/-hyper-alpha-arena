# -*- coding: utf-8 -*-
"""H264 验证 2：反转交易的持有期与止盈/止损幅度。

# 背景

设计（全面升级 v2）P2 = 退出参数重设。做市下 `max_one_side_seconds=7200`（2 小时）、
`take_profit=12`、`stop_loss=80`，都是"等对手盘"语义。

反转交易不同：反转只在 30s~5min 内发生（H255），持有 2 小时是拿反转 alpha 去扛趋势。

本脚本直接回答三个问题：
  1. **持有期**：逆势入场后，持有多久收益最大？（= max_one_side_seconds）
  2. **止盈**：逆势收益的正向分位（反转到位就落袋 = take_profit）
  3. **止损**：逆势收益的负向分位（趋势继续就止损 = stop_loss）

# 口径（纯 tick，无成本，与 H263 一致）

  lookback=120s 判趋势，逆势入场：涨了做空、跌了做多
  持有 H 秒，收益 = −sign(trend) × fwd_H
  1s 网格、30s 去重叠

# 判据

  · 逆势均值随 H 的变化曲线：峰值处 = 最优持有期
  · 若均值在 H=60~120s 见顶、H>300s 衰减 ⇒ 持有期应设 60~120s
  · 止盈 = 正向 P75（多数反转到位的幅度）
  · 止损 = 负向 P25（多数趋势继续的幅度）

# 用法

    python scripts/h264_hold_tp_sl.py --hours 12
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h264_hold_tp_sl.json"
SYMS = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT", "PENDLEUSDT", "SUIUSDT"]
K = 120.0
HOLDS = [30.0, 60.0, 120.0, 300.0, 600.0]
STEP = 30.0


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
    print(f"H264  反转交易持有期与止盈/止损（lookback={K:g}s）")
    print("=" * 104)
    print(f"\n  窗口 {a.hours:g}h")

    # 逐币算逆势收益随 H 的分布
    per_sym = {}
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
        mids = [d[k] for k in ks]
        n = len(ks)
        starts, last = [], -1e18
        for i in range(n):
            if ks[i] - last >= STEP:
                starts.append(i)
                last = ks[i]
        # 每个样本：趋势 + 各 H 的逆势收益
        by_H = {H: [] for H in HOLDS}
        for i in starts:
            j = i
            while j >= 0 and ks[i] - ks[j] < K:
                j -= 1
            if j < 0 or ks[i] - ks[j] < K * 0.9 or mids[j] <= 0:
                continue
            trend = (mids[i] - mids[j]) / mids[j] * 1e4
            sign = -1.0 if trend > 0 else 1.0   # 逆势方向
            for H in HOLDS:
                f = i
                while f + 1 < n and ks[f + 1] - ks[i] < H:
                    f += 1
                if f == i or ks[f] - ks[i] < H * 0.9 or mids[i] <= 0:
                    continue
                fwd = (mids[f] - mids[i]) / mids[i] * 1e4
                by_H[H].append(sign * fwd)
        per_sym[sym.replace("USDT", "")] = by_H

    # ── 一、持有期曲线（逆势均值随 H）──
    print(f"\n{'━'*104}\n  一、逆势收益均值随持有期 H（bp，未扣成本）\n{'━'*104}")
    hdr = "".join(f"{'H='+str(int(x))+'s':>10}" for x in HOLDS)
    print(f"\n  {'symbol':<10}{hdr}")
    for sym, by_H in sorted(per_sym.items()):
        cells = "".join(f"{st.mean(v):>+10.3f}" if v else f"{'—':>10}" for H, v in by_H.items())
        print(f"  {sym:<10}{cells}")
    # 合并（当前四币）
    print(f"\n  {'合并四币':<10}" + "".join(
        f"{st.mean([x for s2 in ('ASTER','XRP','SOL','HYPE') if s2 in per_sym for x in per_sym[s2][H]]):>+10.3f}"
        for H in HOLDS))

    # ── 二、止盈/止损幅度（合并四币，H=60s 的分布）──
    print(f"\n{'━'*104}\n  二、逆势收益分布（H=60s，合并四币）→ 定止盈/止损\n{'━'*104}")
    all60 = []
    for sym in ("ASTER", "XRP", "SOL", "HYPE"):
        if sym in per_sym:
            all60 += per_sym[sym][60.0]
    if all60:
        all60.sort()
        n = len(all60)
        print(f"\n  样本 {n}　均值 {st.mean(all60):+.3f}　中位 {all60[n//2]:+.3f} bp")
        print(f"\n  分位（正=逆势赚、负=逆势亏）：")
        for p in (10, 25, 50, 75, 90):
            print(f"    P{p:<3} {all60[int(n*p/100)]:>+9.3f} bp")
        print(f"\n  ⇒ 建议：")
        print(f"     · 持有期：看上面的均值曲线峰值（若 60~120s 见顶 → 设 60~120s）")
        print(f"     · 止盈 take_profit ≈ P75 = {all60[int(n*0.75)]:+.2f}bp（反转到位）")
        print(f"     · 止损 stop_loss   ≈ P25 = {all60[int(n*0.25)]:+.2f}bp（趋势继续）")
        print(f"     但注意：这是**逆势收益**分布，不是持仓浮盈分布；")
        print(f"     止盈止损应比这些分位略宽（给噪音留余地）。")

    # ── 三、结论 ──
    print(f"\n{'━'*104}\n  三、结论\n{'━'*104}")
    # 找合并四币的最优持有期
    merged = {}
    for H in HOLDS:
        vals = [x for s2 in ("ASTER", "XRP", "SOL", "HYPE") if s2 in per_sym
                for x in per_sym[s2][H]]
        if vals:
            merged[H] = st.mean(vals)
    if merged:
        best_H = max(merged, key=lambda h: merged[h])
        print(f"\n  合并四币逆势均值峰值在 H={best_H:g}s（{merged[best_H]:+.3f}bp）")
        print(f"  ⇒ 持有期建议设 **{best_H:g}s 附近**（当前做市值 7200s 过长）")
        for h, v in merged.items():
            print(f"    H={h:g}s: {v:+.3f}bp")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "k": K,
                               "merged": {str(k): round(v, 4) for k, v in merged.items()},
                               "all60_pct": {str(p): round(all60[int(n*p/100)], 4)
                                             for p in (10, 25, 50, 75, 90)} if all60 else {}},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
