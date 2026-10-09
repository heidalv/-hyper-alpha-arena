# -*- coding: utf-8 -*-
"""H266 验证 4 收尾：counter_trend 真实周期收益（反转时平仓，不是固定持有）。

# 为什么 H265 可能口径错

H265 测的是"挂单成交后**固定 60s** 平仓"的漂移，得到 −1.1bp。
但 counter_trend 的实际退出是：持空头后，**价格回落时减仓侧（买）成交**，
即"反转发生时平仓"，不是"固定 60s 后平仓"。

这两个口径差很大：反转在 60s 内的某刻发生，固定 60s 末可能已经回落到
又反弹了，而"反转时平仓"能吃到回落的那一段。

# 本脚本

对每条逆势腿（H265 的挂单成交），算两种退出口径：
  ① 固定退出：持有 M 秒末平仓（H265 旧口径，参考）
  ② **最优退出**：未来 M 秒内的最有利价平仓（对空头=最低价买回，对多头=最高价卖回）
     —— 这是"完美择时"的上界，真实减仓侧挂单在两者之间

# 判据

  若 ②（最优退出）显著为正，而 ①（固定）为负 ⇒ H265 的口径问题，counter_trend 本身没死
  若 ② 也为负 ⇒ maker 挂单真的吃不到反转，地基有问题

# 用法

    python scripts/h266_reversal_roundtrip.py --hours 12
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h266_reversal_roundtrip.json"
SYMS = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT"]
K = 120.0
M = 60.0
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
    print(f"H266  counter_trend 真实周期收益（lookback={K:g}s → 持有 {M:g}s）")
    print("=" * 104)

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

    # 用 Δ=0.5（接近实盘挂深）测
    D = 0.5
    fixed, optimal = [], []
    hits = tot = 0
    for sym, (ks, mids) in series.items():
        n = len(ks)
        last = -1e18
        for i in range(n):
            if ks[i] - last < STEP:
                continue
            j = i
            while j >= 0 and ks[i] - ks[j] < K:
                j -= 1
            if j < 0 or ks[i] - ks[j] < K * 0.9 or mids[j] <= 0:
                continue
            trend = (mids[i] - mids[j]) / mids[j] * 1e4
            last = ks[i]
            tot += 1
            side = "sell" if trend > 0 else "buy"
            entry = mids[i] * (1.0 + (D / 1e4 if side == "sell" else -D / 1e4))
            # 未来 M 秒内是否触及成交
            f = i + 1
            t_hit = None
            while f < n and ks[f] - ks[i] <= M:
                if (side == "sell" and mids[f] >= entry) or \
                   (side == "buy" and mids[f] <= entry):
                    t_hit = f
                    break
                f += 1
            if t_hit is None:
                continue
            hits += 1
            # 成交后到 M 秒末的价格序列
            seg = []
            f2 = t_hit
            while f2 < n and ks[f2] - ks[i] <= M:
                seg.append(mids[f2])
                f2 += 1
            if not seg:
                continue
            # ① 固定退出：M 秒末
            end_fixed = seg[-1]
            if side == "sell":
                fixed.append((entry - end_fixed) / entry * 1e4)
            else:
                fixed.append((end_fixed - entry) / entry * 1e4)
            # ② 最优退出：M 秒内最有利价
            if side == "sell":
                optimal.append((entry - min(seg)) / entry * 1e4)  # 最低点买回
            else:
                optimal.append((max(seg) - entry) / entry * 1e4)  # 最高点卖回

    print(f"\n  样本 {hits} 条成交（成交率 {hits/tot*100:.1f}%，Δ={D}bp）")
    print(f"\n  {'口径':<16}{'均值bp':>10}{'中位bp':>10}{'P25':>9}{'P75':>9}{'为正%':>9}")
    for lbl, v in (("①固定M秒退出", fixed), ("②最优退出(上界)", optimal)):
        if not v:
            continue
        v = sorted(v)
        pos = sum(1 for x in v if x > 0) / len(v) * 100
        print(f"  {lbl:<16}{st.mean(v):>+10.3f}{st.median(v):>+10.3f}"
              f"{v[len(v)//4]:>+9.3f}{v[3*len(v)//4]:>+9.3f}{pos:>8.1f}%")

    print(f"\n{'━'*104}\n  结论\n{'━'*104}")
    if fixed and optimal:
        mf, mo = st.mean(fixed), st.mean(optimal)
        print(f"\n  固定退出 {mf:+.3f}bp　最优退出 {mo:+.3f}bp")
        if mo > 0 and mf < 0:
            print(f"  ⇒ **口径问题**：反转确实发生（最优退出 +{mo:.3f}），"
                  f"但固定 60s 末已回落又反弹，错过最优退出。")
            print(f"     counter_trend 的真实收益在 {mf:+.3f} ~ {mo:+.3f} 之间"
                  f"（取决于减仓侧挂单的择时）。")
            print(f"     → 需要验证「减仓侧挂单」能吃到多少反转，而不是用固定退出。")
        elif mo < 0:
            print(f"  ⇒ **maker 挂单真的吃不到反转**（最优退出也负 {mo:+.3f}bp）")
            print(f"     → 反转 alpha 只能 taker 立即入场吃到，但 4bp 成本可能盖过它")
        else:
            print(f"  ⇒ 两种口径都正，counter_trend 成立")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "k": K, "m": M, "delta": D,
                               "fixed_mean": round(st.mean(fixed), 4) if fixed else None,
                               "optimal_mean": round(st.mean(optimal), 4) if optimal else None,
                               "n": hits}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
