# -*- coding: utf-8 -*-
"""H227 止损的验证：-80bp 移动之后是**继续**还是**回摆**。

# 为什么这是唯一该问的问题

H226 用 `sign(price_bp)` 给 1,089 条强平归类（分布双峰，|price|<1bp 仅 3.9%）：

    ①″ take_profit  378 腿  **+$94.83**  单腿 **+$0.2509**  price +17.2
    ①′ stop_loss    669 腿  **−$426.71** 单腿 **−$0.6378**  price −26.3
    ⇒ 净额 −$332 全部来自止损路径

而**止损单腿成本**逐日变化极大：

    正常日  09-10 / 09-11   −$0.227 / −$0.221
    坏日    09-15           **−$3.220**（14.2×）
    坏日    09-22           **−$1.186**
    坏日    09-21           −$0.712

同样的出口逻辑，单次成本差一个数量级 ⇒ 成本由**触发时的行情状态**决定。
于是唯一关键的问题：

    **`stop_loss_bp=80` 是在「截断趋势」还是在「回摆前砍仓」？**

  · 80bp 之后**继续**同向（趋势）⇒ 止损是对的，可考虑**收紧**
  · 80bp 之后**回摆**（噪声/插针）⇒ 止损在**低点卖出**，应**放宽或关闭**，
    让减仓侧 maker 单去接（本配置 `stop_maker_grace_sec=0` ⇒ 止损**立即 taker**）

两者修法完全相反，且**不需要新数据**即可判定。

# 数据源（重要更正）

`ticker_snapshots` 在 `alpha_arena` 库里**不存在** —— 市场数据在 **`alpha_market`** 库。
真实高频源 = `asterdex_book_ticker`（1.69 亿行，`bid_px`/`ask_px`/`event_ts_ms`，
实测 10~36 tick/s）。中价 = `(bid_px+ask_px)/2`。

# 算法（避免 O(n²)）

逐 tick 做前后窗搜索是 O(n²)（170 万行会跑不完）。
⇒ 先**降采样到 1 秒**（每币每秒取最后一个 tick 的中价），
再用**双指针**线性推进求 past/fwd。

# 用法

    python scripts/h227_stop_loss_continuation.py --hours 6 --k 60 --m 60
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import statistics as st

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "research_l1" / "out" / "h227_stop_loss_continuation.json"
SYMS = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT"]


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


def q(v, p):
    if not v:
        return 0.0
    s = sorted(v)
    return s[min(len(s) - 1, max(0, int(round(p / 100.0 * (len(s) - 1)))))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--k", type=float, default=60.0)
    ap.add_argument("--m", type=float, default=60.0)
    ap.add_argument("--min-samples", type=int, default=30)
    a = ap.parse_args()

    import psycopg
    with psycopg.connect(market_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, event_ts_ms, (bid_px + ask_px) / 2.0 AS mid
                FROM asterdex_book_ticker
                WHERE ingest_ts >= now() - (%s || ' hours')::interval
                  AND symbol = ANY(%s)
                  AND bid_px > 0 AND ask_px > 0
                ORDER BY symbol, event_ts_ms ASC
            """, (str(float(a.hours)), SYMS))
            rows = cur.fetchall()

    if not rows:
        print("无数据")
        return 1

    # ── 降采样到 1 秒（取该秒最后一个 tick）──
    per = {}
    for sym, ms, mid in rows:
        per.setdefault(str(sym), {})[int(ms // 1000)] = float(mid)
    series = {}
    for sym, d in per.items():
        secs = sorted(d)
        series[sym] = (secs, [d[s] for s in secs])

    print("=" * 104)
    print(f"H227  止损验证：过去 {a.k:g}s 移动 → 未来 {a.m:g}s 移动")
    print("=" * 104)
    for sym, (secs, px) in sorted(series.items()):
        if secs:
            print(f"  {sym:12} 1s 采样 {len(secs):>6} 点　"
                  f"{dt.datetime.fromtimestamp(secs[0]):%H:%M:%S} ~ "
                  f"{dt.datetime.fromtimestamp(secs[-1]):%H:%M:%S}")

    samples = []
    for sym, (secs, px) in series.items():
        n = len(secs)
        # 对每个 i，找**最大的 j < i** 使 `secs[i]-secs[j] >= k`
        # （双指针 j 只前进不后退：k 固定时 i 增大 ⇒ 该 j 也单调不减）
        # 初版把 j 重置成 i 写错了 ⇒ span 永远 0 ⇒ 零样本。
        j = -1
        k2 = 0
        for i in range(n):
            while j + 1 < i and secs[i] - secs[j + 1] >= a.k:
                j += 1
            if j < 0 or secs[i] - secs[j] < a.k:
                continue
            if k2 < i:
                k2 = i
            while k2 < n and secs[k2] - secs[i] < a.m:
                k2 += 1
            if k2 >= n:
                break
            base = px[j]
            if base <= 0:
                continue
            past = (px[i] - base) / base * 1e4
            fwd = (px[k2] - px[i]) / px[i] * 1e4
            samples.append({"sym": sym, "sec": secs[i], "past": past, "fwd": fwd})

    if not samples:
        print(f"\n窗口内不足 {a.k:g}+{a.m:g} 秒的连续样本")
        return 1
    print(f"\n  样本 {len(samples)}（每条 = 一个 (币, 秒) 的 past/fwd 对）")

    # ── 一、分桶 ──
    print(f"\n{'━'*104}\n  一、按「过去 {a.k:g}s 移动」分桶 → 未来 {a.m:g}s 移动\n{'━'*104}")
    EDGES = [-1e9, -100, -80, -60, -40, -20, -10, -5, 5, 10, 20, 40, 60, 80, 100, 1e9]
    print(f"\n  {'过去移动bp':>16}{'样本':>8}{'未来均值bp':>12}{'未来中位bp':>12}"
          f"{'胜率(未来>0)':>13}{'未来P10':>10}{'未来P90':>10}")
    bands = []
    for i in range(len(EDGES) - 1):
        lo, hi = EDGES[i], EDGES[i + 1]
        sel = [x for x in samples if lo <= x["past"] < hi]
        if len(sel) < a.min_samples:
            continue
        fw = [x["fwd"] for x in sel]
        win = sum(1 for x in fw if x > 0) / len(fw) * 100
        lbl = (f"≤{hi:g}" if i == 0 else
               (f"≥{lo:g}" if i == len(EDGES) - 2 else f"{lo:g}~{hi:g}"))
        print(f"  {lbl:>16}{len(sel):>8}{st.mean(fw):>+12.3f}{st.median(fw):>+12.3f}"
              f"{win:>12.1f}%{q(fw,10):>+10.2f}{q(fw,90):>+10.2f}")
        bands.append({"band": lbl, "n": len(sel), "fwd_mean": round(st.mean(fw), 4),
                      "fwd_median": round(st.median(fw), 4), "win_pct": round(win, 2)})

    # ── 二、诊断 ──
    print(f"\n{'━'*104}\n  二、诊断：止损阈值处是「继续」还是「回摆」\n{'━'*104}")
    verdicts = []
    for thr in (40, 60, 80, 100):
        dn = [x for x in samples if x["past"] <= -thr]
        up = [x for x in samples if x["past"] >= thr]
        if len(dn) < a.min_samples:
            print(f"\n  ±{thr}bp：逆向样本不足（{len(dn)}）")
            continue
        fd = [x["fwd"] for x in dn]
        mr, md = st.mean(fd), st.median(fd)
        wr = sum(1 for x in fd if x > 0) / len(fd) * 100
        print(f"\n  ── 逆向 ≥{thr}bp（多头视角：价格跌了 {thr}bp 以上）")
        print(f"     {len(dn)} 样本　未来均值 **{mr:+.3f} bp**　中位 {md:+.3f}　"
              f"胜率(未来>0) {wr:.1f}%　"
              f"P10 {q(fd,10):+.2f}　P90 {q(fd,90):+.2f}")
        if mr > 0 and wr > 50:
            v = "回摆（止损在低点卖出 ⇒ 应放宽/改 maker 宽限）"
        elif mr < 0 and wr < 50:
            v = "继续（止损正确 ⇒ 可考虑收紧以减少单次亏损）"
        else:
            v = "混合（均值与胜率方向不一致 ⇒ 不能只凭其一）"
        print(f"     ⇒ **{v}**")
        verdicts.append({"thr": thr, "n": len(dn), "fwd_mean": round(mr, 4),
                         "fwd_median": round(md, 4), "win_pct": round(wr, 2),
                         "verdict": v})
        if len(up) >= a.min_samples:
            fu = [x["fwd"] for x in up]
            print(f"     （对称参考：正向 ≥{thr}bp 的 {len(up)} 样本，"
                  f"未来均值 {st.mean(fu):+.3f} bp）")

    # ── 三、逐小时稳定性 ──
    print(f"\n{'━'*104}\n  三、逐小时：逆向 ≥80bp 后未来移动是否稳定同号（跨窗口判据）\n{'━'*104}")
    hrs = {}
    for x in samples:
        if x["past"] <= -80:
            hrs.setdefault(dt.datetime.fromtimestamp(x["sec"]).strftime("%m-%d %H:00"),
                           []).append(x["fwd"])
    print(f"\n  {'小时':<14}{'样本':>7}{'未来均值bp':>12}{'未来中位bp':>12}{'胜率':>8}")
    pos = 0
    nh = 0
    for h in sorted(hrs):
        v = hrs[h]
        if len(v) < 5:
            continue
        nh += 1
        if st.mean(v) > 0:
            pos += 1
        print(f"  {h:<14}{len(v):>7}{st.mean(v):>+12.3f}{st.median(v):>+12.3f}"
              f"{sum(1 for z in v if z>0)/len(v)*100:>7.1f}%")
    if nh:
        print(f"\n  ⇒ 未来均值为正的小时数 = **{pos}/{nh}**")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "k": a.k, "m": a.m,
                               "samples": len(samples), "bands": bands,
                               "verdicts": verdicts,
                               "pos_hours": pos, "n_hours": nh},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
