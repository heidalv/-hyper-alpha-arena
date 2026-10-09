# -*- coding: utf-8 -*-
"""H249 按**市场价差分档**的反事实：「只在价差够宽时做」能不能转正。

# 为什么这是最后一条没试过的路

H247 的模型（用引擎真实的撤单节奏）**校准到 3.8%**
（Δ=0.2 模拟 −$0.0354/腿 vs 账本实测 −$0.0368/腿）⇒ 可信。

它给的结论是：

    条件净额 = Δ + term，而 **term ≈ −1.3bp 且与 Δ 无关**
    ⇒ 转正条件 **Δ > 1.3bp**
    而 maker 挂深的**物理上限 = 市场半价差 = 0.625bp（P50）**
    ⇒ 永远达不到 ⇒ 全线负值

**但 H248 的副产品给出了一线**：半价差的 **P90 = 0.86~1.37bp**，
**已经越过 1.3bp 的转正点**。

⇒ 所以"挂多深"不是杠杆（做不到），**"在什么价差水平做"才是**。

# 本脚本测什么

对每个时刻算市场半价差 `hs`，把样本按 `hs` 分档，逐档跑 H247 的同一模型：

    Δ = spread_mult × hs        （引擎的真实公式；spread_mult ∈ {0.5, 1.0}）
    成交判定：挂单存活一个 tick（引擎语义），价格触及 ⇒ 成交
    净额 = Δ + term（离场按窗末中价 = 理想 maker）

⇒ 输出**逐档的每 tick 期望 $/腿**。若高档位转正、低档位为负，
就得到一个非常干净的闸门：**`hs < 阈值 ⇒ 不挂单`**。

# 与既有闸门的区别（为什么这是新的）

| 闸门 | 判据 | 能解决本问题吗 |
|---|---|---|
| `vol_pause_sigma` | 已实现**波动**（5min 窗） | ❌ 波动大 ≠ 价差宽 |
| `vol_pause_mult` | 波动 vs 基准（关闭中） | ❌ 同上 |
| `trend_pause_bp` | 净移动（6.7min 窗） | ❌ 与价差无关 |
| **本脚本** | **当前市场价差** | ✅ **直接对标 Δ 的物理上限** |

**逆选择是常数量（1.3bp），而可收价差是变量（0.4~2.7bp）**
⇒ 唯一能改变符号的就是"挑价差宽的时刻做"。这与"找方向信号"**无关**
（本会话 6 次方向信号全失败，但这不是方向信号，是成本/收益比）。

# 用法

    python scripts/h249_spread_conditional.py --hours 24
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h249_spread_conditional.json"
CUR = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT"]


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
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--tick-sec", type=float, default=19.0)
    ap.add_argument("--stride", type=int, default=19)
    ap.add_argument("--notional", type=float, default=250.0,
                    help="单腿名义（实测中位 $250）")
    a = ap.parse_args()

    import psycopg
    # ⚠️ 必须**逐币拉 + SQL 端降采样**：24h × 4 币的原始 tick 是 810 万行 × 4 列，
    # 一次性拉会让服务端在 COMMIT 时切断连接
    # （实测 `OperationalError: server closed the connection unexpectedly`）。
    # 按 1 秒取末值后每币只剩 ~8.6 万行，4 币合计 ~35 万行，安全。
    RAW = {}
    for sym in CUR:
        with psycopg.connect(market_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ON (bucket) bucket, bid_px, ask_px
                    FROM (
                        SELECT (event_ts_ms / 1000) AS bucket, bid_px, ask_px
                        FROM asterdex_book_ticker
                        WHERE ingest_ts >= now() - (%s || ' hours')::interval
                          AND symbol = %s AND bid_px > 0 AND ask_px > bid_px
                    ) t
                    ORDER BY bucket, bid_px
                """, (str(float(a.hours) + 0.2), sym))
                RAW[sym] = cur.fetchall()
    rows_n = sum(len(v) for v in RAW.values())
    if not rows_n:
        print("无数据")
        return 1

    g = {}
    for sym, recs in RAW.items():
        g[sym] = {int(b): (float(bid), float(ask)) for b, bid, ask in recs}
    series = {}
    for s, d in g.items():
        ks = sorted(d)
        mids, hss = [], []
        for k in ks:
            b, a_ = d[k]
            mids.append((b + a_) / 2.0)
            hss.append((a_ - b) / 2.0 / ((b + a_) / 2.0) * 1e4)
        series[s] = (ks, mids, hss)
    _ = rows_n

    print("=" * 104)
    print("H249  按市场价差分档的反事实（Δ = spread_mult × 市场半价差）")
    print("=" * 104)
    print(f"\n  窗口 {a.hours:g}h　挂单存活 {a.tick_sec:g}s　单腿名义 ${a.notional:g}")
    allhs = []
    for s, (ks, mids, hss) in sorted(series.items()):
        allhs += hss
        print(f"  {s:12} {len(ks):>7} 点　半价差 P50 {st.median(hss):.4f} bp")
    print(f"\n  全体半价差分位：P10 {sorted(allhs)[len(allhs)//10]:.4f}　"
          f"P50 {st.median(allhs):.4f}　"
          f"P90 {sorted(allhs)[int(len(allhs)*0.9)]:.4f}　"
          f"P99 {sorted(allhs)[int(len(allhs)*0.99)]:.4f}")

    # 分档边界（用全体分位，保证每档样本量可比）
    srt = sorted(allhs)
    EDGES = [0.0] + [srt[int(len(srt) * p)] for p in (0.2, 0.4, 0.6, 0.8, 0.95)] + [1e9]

    for SM in (0.5, 1.0):
        print(f"\n{'━'*104}\n  spread_mult = {SM:g}（Δ = {SM:g} × 半价差）\n{'━'*104}")
        print(f"\n  {'半价差档(bp)':>16}{'样本':>8}{'Δ中位bp':>10}{'成交率':>9}"
              f"{'条件净额bp':>13}{'每tick期望bp':>15}{'**折每腿$**':>13}")
        agg = []
        for i in range(len(EDGES) - 1):
            lo, hi = EDGES[i], EDGES[i + 1]
            tot = hits = 0
            terms, deltas = [], []
            for sym, (ks, mids, hss) in series.items():
                n = len(ks)
                for t in range(0, n - 2, a.stride):
                    hs = hss[t]
                    if not (lo <= hs < hi):
                        continue
                    tot += 1
                    D = SM * hs
                    bid_q = mids[t] * (1.0 - D / 1e4)
                    ask_q = mids[t] * (1.0 + D / 1e4)
                    end = ks[t] + a.tick_sec
                    j = t + 1
                    tb = ts_ = None
                    while j < n and ks[j] <= end:
                        if tb is None and mids[j] <= bid_q:
                            tb = j
                        if ts_ is None and mids[j] >= ask_q:
                            ts_ = j
                        j += 1
                    if tb is None and ts_ is None:
                        continue
                    hits += 1
                    if tb is not None and (ts_ is None or tb <= ts_):
                        side, ti, entry = "buy", tb, bid_q
                    else:
                        side, ti, entry = "sell", ts_, ask_q
                    sign = 1.0 if side == "buy" else -1.0
                    j2 = ti
                    while j2 + 1 < n and ks[j2 + 1] <= end:
                        j2 += 1
                    terms.append(sign * (mids[j2] - entry) / entry * 1e4)
                    deltas.append(D)
                # end for sym
            if not tot or not terms:
                print(f"  {'':>16}{tot:>8}  （无成交）")
                continue
            hr = hits / tot
            dmed = st.median(deltas)
            cond = st.mean(deltas) + st.mean(terms)
            exp = cond * hr
            usd = exp / 1e4 * a.notional
            lbl = (f"{lo:.3f}–{hi:.3f}" if hi < 1e9 else f"{lo:.3f}+")
            print(f"  {lbl:>16}{tot:>8}{dmed:>10.4f}{hr*100:>8.2f}%"
                  f"{cond:>+13.3f}{exp:>+15.3f}{usd:>+13.4f}")
            agg.append({"lo": round(lo, 4), "hi": round(hi, 4) if hi < 1e9 else None,
                        "n": tot, "hit_pct": round(hr * 100, 2),
                        "delta_med": round(dmed, 4), "cond_bp": round(cond, 4),
                        "usd_per_leg": round(usd, 5)})
        pos = [r for r in agg if r["usd_per_leg"] > 0]
        if pos:
            lo_ok = min(r["lo"] for r in pos)
            print(f"\n  ⇒ **转正的最低半价差档 = {lo_ok:.3f}bp 起**"
                  f"（{len(pos)}/{len(agg)} 档为正）")
            print(f"     ⇒ 可实现的闸门：**市场半价差 < {lo_ok:.2f}bp ⇒ 不挂单**")
            # 覆盖度：这些档占多少时间
            cov = sum(r["n"] for r in pos) / sum(r["n"] for r in agg) * 100
            print(f"     覆盖时间占比 = **{cov:.1f}%**")
            if cov < 10:
                print(f"     ⚠️ 覆盖 < 10% ⇒ 车道会大部分时间不报价（等于基本停业）")
        else:
            print(f"\n  ⇒ **没有任何价差档转正** ⇒ 这条路也走不通")
        print(f"\n  ⚠️ 口径限制：无队列（深度挂单成交率高估）；离场按窗末中价（理想 maker）")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "tick_sec": a.tick_sec,
                               "stride": a.stride, "notional": a.notional,
                               "edges": [round(x, 4) if x < 1e8 else None for x in EDGES]},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
