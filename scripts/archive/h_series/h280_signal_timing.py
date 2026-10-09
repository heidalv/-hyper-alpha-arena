# -*- coding: utf-8 -*-
"""H280 信号时机扫描：当前宇宙上，反转 alpha 到底在哪个 (lookback, forward) 格子里活着。

# 背景（用户指令：系统性研究这个方向，先研究调研学习再建学习进化系统）

  现状：固定规则 = 过去 120s 涨→卖 / 跌→买（15s 采样）。
  用户反馈：脆弱（早晨趋势段亏）且滞后（alpha 薄）。
  本脚本把"时机"参数化扫描：过去 k 秒 → 未来 m 秒的 corr，
  k ∈ {15,30,60,90,120,180,300}，m ∈ {30,60,90,120,180,300}，
  找出当前宇宙（SOL/DOGE/ETH/BNB）最近 48h 里反转最强的格子
  —— 这就是"信号时机升级"的第一手证据。

# 口径（沿用 H255 硬规矩）

  · 1s 价格网格（book_ticker 每币每秒最新 bid/ask → mid）
  · 样本 60s 间隔去重叠；每格报独立样本数，n<500 标记不足
  · corr 符号 + t 值；跨币符号一致性才认
  · 48h 窗口（含夜盘与早晨两个 regime，不挑时段）

# 用法

    python scripts/h280_signal_timing.py --hours 48
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h280_signal_timing.json"
CUR = ["SOLUSDT", "DOGEUSDT", "ETHUSDT", "BNBUSDT"]
KS = [15.0, 30.0, 60.0, 90.0, 120.0, 180.0, 300.0]
MS = [30.0, 60.0, 90.0, 120.0, 180.0, 300.0]


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


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0, 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0 or syy <= 0:
        return 0.0, 0.0
    r = sxy / (sxx * syy) ** 0.5
    if abs(r) >= 1:
        t = float("inf") if r > 0 else float("-inf")
    else:
        t = r * ((n - 2) / (1 - r * r)) ** 0.5
    return r, t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=48.0)
    ap.add_argument("--ago", type=float, default=0.0,
                    help="窗口整体前移小时数（交叉验证用：ago=48 = 2~4 天前窗口）")
    ap.add_argument("--step", type=float, default=60.0, help="样本间隔（秒，去重叠）")
    a = ap.parse_args()

    import psycopg

    series = {}
    for sym in CUR:
        try:
            with psycopg.connect(market_dsn()) as c:
                with c.cursor() as cur:
                    cur.execute("""
                        SELECT DISTINCT ON (bucket) bucket, bid_px, ask_px
                        FROM (SELECT (event_ts_ms/1000) AS bucket, bid_px, ask_px
                              FROM asterdex_book_ticker
                              WHERE event_ts_ms >= (extract(epoch from now())*1000 - (%s+%s)*3600*1000)::bigint
                                AND event_ts_ms <  (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                                AND symbol = %s AND bid_px>0 AND ask_px>bid_px) t
                        ORDER BY bucket, bid_px
                    """, (a.hours, a.ago, a.ago, sym))
                    recs = cur.fetchall()
            d = {int(b): ((float(x) + float(y)) / 2.0) for b, x, y in recs}
            ks_sorted = sorted(d)
            series[sym] = (ks_sorted, [d[k] for k in ks_sorted])
            print(f"  {sym:10} {len(ks_sorted):>8} 点  "
                  f"{datetime.fromtimestamp(ks_sorted[0]):%m-%d %H:%M} ~ {datetime.fromtimestamp(ks_sorted[-1]):%m-%d %H:%M}",
                  flush=True)
        except Exception as e:
            print(f"  ⚠️ {sym}: {str(e)[:70]}", flush=True)

    print("=" * 104)
    print("H280  信号时机扫描：corr(past k 秒 → fwd m 秒)，负 = 反转")
    print("=" * 104)

    results = {}
    for sym, (ks_sorted, px) in sorted(series.items()):
        n = len(ks_sorted)
        starts = []
        last = -1e18
        for i in range(n):
            if ks_sorted[i] - last >= a.step:
                starts.append(i)
                last = ks_sorted[i]
        rows = []
        for k in KS:
            for m in MS:
                xs, ys = [], []
                for i in starts:
                    j = i
                    while j >= 0 and ks_sorted[i] - ks_sorted[j] < k:
                        j -= 1
                    if j < 0 or ks_sorted[i] - ks_sorted[j] < k * 0.9 or px[j] <= 0:
                        continue
                    f = i
                    while f + 1 < n and ks_sorted[f + 1] - ks_sorted[i] < m:
                        f += 1
                    if f == i or ks_sorted[f] - ks_sorted[i] < m * 0.9 or px[i] <= 0:
                        continue
                    past = (px[i] - px[j]) / px[j] * 1e4
                    fwd = (px[f] - px[i]) / px[i] * 1e4
                    xs.append(past)
                    ys.append(fwd)
                r, t = pearson(xs, ys)
                rows.append({"k": int(k), "m": int(m), "corr": round(r, 5),
                             "t": round(t, 2) if abs(t) < 1e6 else t, "n": len(xs)})
                results[(sym, int(k), int(m))] = (r, t, len(xs))

        print(f"\n  ── {sym} ──")
        hdr = "".join(f"{'m=' + str(int(x)) + 's':>12}" for x in MS)
        print(f"  {'k\\m':>8}{hdr}")
        for k in KS:
            cells = ""
            for m in MS:
                r, t, nn = results[(sym, int(k), int(m))]
                mark = "*" if nn < 500 else " "
                cells += f"{r:>10.4f}{mark} "
            print(f"  {'k=' + str(int(k)) + 's':>8}{cells}")

    # 合并四币（逐格合并样本）
    print(f"\n{'━' * 104}\n  合并四币\n{'━' * 104}")
    hdr = "".join(f"{'m=' + str(int(x)) + 's':>12}" for x in MS)
    print(f"  {'k\\m':>8}{hdr}")
    best = None
    for k in KS:
        cells = ""
        for m in MS:
            xs, ys = [], []
            for sym in series:
                pass  # 重新合并样本：这里直接汇总各币样本不可行，改逐格从结果反推 → 用重算
            cells += f"{'':>12}"
        print(f"  {'k=' + str(int(k)) + 's':>8}{cells}")

    # 合并版：逐格重算（合并四币样本，正确口径）
    merged = {}
    all_series = {sym: (ks_sorted, px) for sym, (ks_sorted, px) in series.items()}
    for k in KS:
        for m in MS:
            xs, ys = [], []
            for sym, (ks_sorted, px) in all_series.items():
                n = len(ks_sorted)
                starts = []
                last = -1e18
                for i in range(n):
                    if ks_sorted[i] - last >= a.step:
                        starts.append(i)
                        last = ks_sorted[i]
                for i in starts:
                    j = i
                    while j >= 0 and ks_sorted[i] - ks_sorted[j] < k:
                        j -= 1
                    if j < 0 or ks_sorted[i] - ks_sorted[j] < k * 0.9 or px[j] <= 0:
                        continue
                    f = i
                    while f + 1 < n and ks_sorted[f + 1] - ks_sorted[i] < m:
                        f += 1
                    if f == i or ks_sorted[f] - ks_sorted[i] < m * 0.9 or px[i] <= 0:
                        continue
                    xs.append((px[i] - px[j]) / px[j] * 1e4)
                    ys.append((px[f] - px[i]) / px[i] * 1e4)
            r, t = pearson(xs, ys)
            merged[(int(k), int(m))] = (r, t, len(xs))
            if best is None or (len(xs) >= 500 and r < best[0]):
                best = (r, t, len(xs), int(k), int(m))

    print(f"\n  {'k\\m':>8}{hdr}")
    for k in KS:
        cells = ""
        for m in MS:
            r, t, nn = merged[(int(k), int(m))]
            mark = "*" if nn < 500 else " "
            cells += f"{r:>10.4f}{mark} "
        print(f"  {'k=' + str(int(k)) + 's':>8}{cells}")
    r, t, nn, bk, bm = best
    print(f"\n  反转最强格（n≥500）：k={bk}s m={bm}s  corr={r:.4f}  t={t:.1f}  n={nn}")
    print(f"  对比现状规则：k=120s m=60s → corr={merged.get((120,60),(0,0,0))[0]:.4f}"
          f"  t={merged.get((120,60),(0,0,0))[1]:.1f}  n={merged.get((120,60),(0,0,0))[2]}")

    OUT.write_text(json.dumps({
        "window_h": a.hours, "symbols": CUR, "step": a.step,
        "per_symbol": {f"{s}|k{k}|m{m}": {"corr": v[0], "t": v[1], "n": v[2]}
                       for (s, k, m), v in results.items()},
        "merged": {f"k{k}|m{m}": {"corr": v[0], "t": v[1], "n": v[2]}
                   for (k, m), v in merged.items()},
        "best": {"k": bk, "m": bm, "corr": r, "t": t, "n": nn},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
