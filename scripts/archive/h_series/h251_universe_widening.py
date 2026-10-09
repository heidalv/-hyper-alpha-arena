# -*- coding: utf-8 -*-
"""H251 换宇宙的可行性验证：宽价差标的能否让做市转正。

# 这是唯一有直接算术依据的方向

全链条（本会话已证，模型校准 3.8%）：

    单腿 P&L = Δ − 逆选择      Δ = 我们离中价的挂深
    逆选择 ≈ 常数 1.3bp        （与 Δ 无关，四个深度一致）
    ⇒ 转正条件 **Δ > 1.3bp**
    而 maker 挂深的物理上限 = **市场半价差**（"不穿越钳制"硬编码 `_lo = best_bid`）
    ⇒ **需要半价差 > 1.3bp**

实测当前宇宙（24h，百万级样本）：

    ASTER P50 0.6853 / XRP 0.6657 / HYPE 0.5841 / SOL 0.4273 bp
    ⇒ **全部不满足**（最好的 ASTER 也差 1.9 倍）

而仓库自己的历史记录（F291 注释）指出过宽价差候选：

    「ADA 5.15bp / UNI 4.73 / AVAX 4.11 / LINK 2.76 vs **ETH 0.04bp**」

⇒ 若那些标的的半价差确实 ≥1.3bp（甚至 2.8~5.2bp），
   **Δ 的上限就从 0.63bp 抬到 2.8~5.2bp，越过转正点。**

# 本脚本做什么

对**候选标的**跑与 H247/H249 相同的模型：

1. 半价差分布（P50/P90/P99）—— 决定 Δ 的上限
2. 逐档位的成交率、条件净额、**折每腿 $**
3. 与当前四币**同一口径对照**（同一时间段，避免 regime 差异）

# 候选标的怎么选（不猜）

从 `symbol_catalog` / 已有宇宙候选里取有 tick 数据的：
`asterdex_book_ticker` 里出现过的全部 symbol，按半价差中位数排序。

⚠️ 但**必须同时看流动性**：宽价差可能只是因为**没人交易**，
那样成交率会塌到 0，Δ 再高也没用。所以同时给**每币的 tick 频率**。

# 用法

    python scripts/h251_universe_widening.py --hours 12 --top 20
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h251_universe_widening.json"
NOW = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT"]


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
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--tick-sec", type=float, default=15.0)
    ap.add_argument("--stride", type=int, default=15)
    ap.add_argument("--notional", type=float, default=250.0)
    a = ap.parse_args()

    import psycopg
    with psycopg.connect(market_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, count(*) AS n
                FROM asterdex_book_ticker
                WHERE ingest_ts >= now() - (%s || ' hours')::interval
                  AND bid_px > 0 AND ask_px > bid_px
                GROUP BY 1 HAVING count(*) > 20000
                ORDER BY 2 DESC
            """, (str(float(a.hours) + 0.2),))
            cand = cur.fetchall()
    if not cand:
        print("无候选")
        return 1

    print("=" * 104)
    print("H251  换宇宙可行性：宽价差标的能否让做市转正")
    print("=" * 104)
    print(f"\n  窗口 {a.hours:g}h　有 tick 数据的 symbol（>2万行）= {len(cand)} 个")

    # 逐 symbol 取 1s 降采样，算半价差
    syms = [s for s, _ in cand]
    prof = {}
    RAW = {}
    for sym in syms:
        try:
            with psycopg.connect(market_dsn()) as c:
                with c.cursor() as cur:
                    cur.execute("""
                        SELECT DISTINCT ON (bucket) bucket, bid_px, ask_px
                        FROM (SELECT (event_ts_ms/1000) AS bucket, bid_px, ask_px
                              FROM asterdex_book_ticker
                              WHERE ingest_ts >= now() - (%s || ' hours')::interval
                                AND symbol = %s AND bid_px > 0 AND ask_px > bid_px) t
                        ORDER BY bucket, bid_px
                    """, (str(float(a.hours) + 0.2), sym))
                    RAW[sym] = cur.fetchall()
        except Exception as e:
            print(f"  ⚠️ {sym} 拉取失败：{str(e)[:60]}")
            continue

    for sym, recs in RAW.items():
        if len(recs) < 100:
            continue
        d = {int(b): ((float(bi) + float(ak)) / 2.0,
                      (float(ak) - float(bi)) / 2.0 / ((float(bi) + float(ak)) / 2.0) * 1e4)
             for b, bi, ak in recs}
        ks = sorted(d)
        mids = [d[k][0] for k in ks]
        hss = [d[k][1] for k in ks]
        span_h = (ks[-1] - ks[0]) / 3600.0 if len(ks) > 1 else 0
        prof[sym] = {"ks": ks, "mids": mids, "hss": hss, "span_h": span_h,
                     "hs_p50": st.median(hss),
                     "hs_p90": sorted(hss)[int(len(hss) * 0.9)],
                     "tick_per_s": len(recs) / max(1e-9, span_h * 3600)}

    print(f"\n{'━'*104}\n  一、候选标的的半价差与流动性（按半价差 P50 排序）\n{'━'*104}")
    print(f"\n  {'symbol':<14}{'半价差P50':>11}{'P90':>9}{'tick/s':>9}"
          f"{'当前宇宙':>10}{'Δ上限达标?':>12}")
    order = sorted(prof, key=lambda s: -prof[s]["hs_p50"])
    for s in order[:a.top]:
        p = prof[s]
        ok = "✓ 是" if p["hs_p50"] > 1.3 else "✗ 否"
        print(f"  {s:<14}{p['hs_p50']:>11.4f}{p['hs_p90']:>9.4f}"
              f"{p['tick_per_s']:>9.2f}{'✓' if s in NOW else '':>10}{ok:>12}")
    print(f"\n  （转正需要半价差 > 1.3bp；`tick/s` 是流动性代理，太低则成交率会塌）")

    # ── 二、对候选跑同一模型 ──
    print(f"\n{'━'*104}\n  二、对 Δ = 1.0 × 半价差 跑同一模型（挂单存活 {a.tick_sec:g}s）\n{'━'*104}")
    print(f"\n  {'symbol':<14}{'半价差P50':>11}{'Δ中位bp':>10}{'成交率':>9}"
          f"{'条件净额bp':>13}{'**折每腿$**':>13}{'判定':>10}")
    rows = []
    for s in order[:a.top]:
        p = prof[s]
        ks, mids, hss = p["ks"], p["mids"], p["hss"]
        n = len(ks)
        hits = tot = 0
        terms, deltas = [], []
        for t in range(0, n - 2, a.stride):
            hs = hss[t]
            D = 1.0 * hs
            tot += 1
            bq = mids[t] * (1.0 - D / 1e4)
            aq = mids[t] * (1.0 + D / 1e4)
            end = ks[t] + a.tick_sec
            j = t + 1
            tb = tsl = None
            while j < n and ks[j] <= end:
                if tb is None and mids[j] <= bq:
                    tb = j
                if tsl is None and mids[j] >= aq:
                    tsl = j
                j += 1
            if tb is None and tsl is None:
                continue
            hits += 1
            if tb is not None and (tsl is None or tb <= tsl):
                side, ti, entry = "buy", tb, bq
            else:
                side, ti, entry = "sell", tsl, aq
            sign = 1.0 if side == "buy" else -1.0
            j2 = ti
            while j2 + 1 < n and ks[j2 + 1] <= end:
                j2 += 1
            terms.append(sign * (mids[j2] - entry) / entry * 1e4)
            deltas.append(D)
        if not tot or not terms:
            print(f"  {s:<14}{p['hs_p50']:>11.4f}  （无成交）")
            continue
        hr = hits / tot
        cond = st.mean(deltas) + st.mean(terms)
        usd = cond * hr / 1e4 * a.notional
        verdict = "✓ 正" if usd > 0 else "✗ 负"
        mark = " ←当前" if s in NOW else ""
        print(f"  {s:<14}{p['hs_p50']:>11.4f}{st.median(deltas):>10.4f}"
              f"{hr*100:>8.2f}%{cond:>+13.3f}{usd:>+13.4f}{verdict:>10}{mark}")
        rows.append({"symbol": s, "hs_p50": round(p["hs_p50"], 4),
                     "hit_pct": round(hr * 100, 3), "cond_bp": round(cond, 4),
                     "usd_per_leg": round(usd, 5),
                     "current": s in NOW, "tick_per_s": round(p["tick_per_s"], 3)})

    # ── 三、结论 ──
    print(f"\n{'━'*104}\n  三、结论\n{'━'*104}")
    pos = [r for r in rows if r["usd_per_leg"] > 0]
    cur = [r for r in rows if r["current"]]
    if cur:
        _s = ", ".join(f"{r['symbol'].replace('USDT','')}={r['usd_per_leg']:+.4f}"
                       for r in cur)
        print(f"\n  当前四币的每腿期望：{_s}")
    if pos:
        print(f"\n  ⇒ **有 {len(pos)} 个标的转正**：")
        for r in sorted(pos, key=lambda x: -x["usd_per_leg"])[:10]:
            print(f"     {r['symbol']:<14} 半价差 {r['hs_p50']:.3f}bp　"
                  f"成交率 {r['hit_pct']:.2f}%　**${r['usd_per_leg']:+.4f}/腿**　"
                  f"tick/s {r['tick_per_s']:.2f}")
        print(f"\n  ⚠️ 但必须同时看成交率与 tick/s：")
        print(f"     · 成交率太低（<1%）⇒ 一天成交几次，统计上没意义")
        print(f"     · tick/s 太低 ⇒ 宽价差只是因为**没人交易**，不是真有价值")
    else:
        print(f"\n  ⇒ **没有任何候选标的转正** ⇒ 换宇宙也解决不了")
    print(f"\n  ⚠️ 口径限制（同 H247）：无队列（高估成交率）；"
          f"离场按窗末中价（理想 maker）；未建模换币后的库存出库成本")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "tick_sec": a.tick_sec,
                               "notional": a.notional, "rows": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
