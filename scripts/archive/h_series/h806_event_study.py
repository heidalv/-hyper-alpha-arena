# -*- coding: utf-8 -*-
"""[h806 2026-10-04 桥上报执行] S1 入场信号事件研究 + 90s TP/SL 重定。

对宇宙 6 币近 24h 的每个 30s 采样点:
  · trend300 = 300s 中价动量;
  · ofi60    = 过去 60s 成交的方向失衡(买主动=+,卖主动=−,净/总);
  · markout90 = 此后 90s 的中价漂移。
流脉冲集 = |ofi60| ≥ 0.15。按 (ofi×trend 同向?) 分桶,统计各桶
"按 ofi 方向进场"的期望 90s 漂移 ⇒ 找出**正期望的入场组合**;
并给出 90s 漂移的分布分位 ⇒ TP/SL 按 90s 波幅重定。
"""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from backend.services.market_maker.attribution import _market_dsn  # noqa: E402
import psycopg  # noqa: E402

UNI = ["1000SHIB", "NEAR", "PENGU", "CBRS", "COIN", "RESOLV"]
TH = 0.15


def main() -> int:
    now_ms = int(time.time() * 1000)
    lo_ms = now_ms - 24 * 3600 * 1000
    rows = []
    with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
        for s in UNI:
            sym = s + "USDT"
            cur.execute(
                "SELECT event_ts_ms, price, qty, is_buyer_maker FROM asterdex_trades"
                " WHERE symbol=%s AND event_ts_ms > %s ORDER BY event_ts_ms",
                (sym, lo_ms))
            tr = [(float(r[0]), float(r[1]), float(r[2]), bool(r[3])) for r in cur.fetchall()]
            cur.execute(
                "SELECT event_ts_ms, (bid_px+ask_px)/2.0 FROM asterdex_book_ticker"
                " WHERE symbol=%s AND event_ts_ms > %s AND bid_px>0 AND ask_px>bid_px"
                " ORDER BY event_ts_ms", (sym, lo_ms))
            bt = [(float(r[0]), float(r[1])) for r in cur.fetchall()]
            rows.append((s, tr, bt))
            print(f"  {s:<10} trades={len(tr)} book={len(bt)}")

    # 采样网格:每 30s;对每个样本找最近的前后 mid
    grid_ms = range(lo_ms + 300000, now_ms - 90000, 30000)
    samples = []
    for s, tr, bt in rows:
        if not bt:
            continue
        for t in grid_ms:
            # mid_now:最后一个 ≤t 的 mid
            mids = [m for ts, m in bt if ts <= t]
            if not mids:
                continue
            mid_now = mids[-1]
            mid_300 = next((m for ts, m in zip([x[0] for x in bt], [x[1] for x in bt])
                            if ts <= t - 300000), mid_now)
            mid_90 = next((m for ts, m in zip([x[0] for x in bt], [x[1] for x in bt])
                           if ts >= t + 90000), mid_now)
            if mid_now <= 0 or mid_90 <= 0:
                continue
            buy_v = sum(q * p for ts, p, q, bm in tr if t - 60000 <= ts <= t and not bm)
            sell_v = sum(q * p for ts, p, q, bm in tr if t - 60000 <= ts <= t and bm)
            tot = buy_v + sell_v
            if tot <= 0:
                continue
            ofi = (buy_v - sell_v) / tot
            trend300 = (mid_now - mid_300) / mid_300 * 1e4
            mo90 = (mid_90 - mid_now) / mid_now * 1e4
            samples.append((s, ofi, trend300, mo90))

    pulses = [x for x in samples if abs(x[1]) >= TH]
    agree = [x for x in pulses if x[1] * x[2] > 0]
    dis = [x for x in pulses if x[1] * x[2] < 0]
    all_mo = [x[3] for x in samples]
    agree_mo = [(x[1], x[3]) for x in agree]
    print(f"\n== 采样 {len(samples)} 点 | 流脉冲 {len(pulses)} | 同向 {len(agree)} | 反向 {len(dis)} ==")
    if agree_mo:
        exp = sum(mo for ofi, mo in agree_mo) / len(agree_mo)
        print(f"  同向桶:按 ofi 方向进场的期望 90s 漂移 = {exp:+.2f}bp(n={len(agree_mo)})")
    if dis:
        expd = sum(x[3] for x in dis) / len(dis)
        print(f"  反向桶:按 ofi 方向进场的期望 90s 漂移 = {expd:+.2f}bp(n={len(dis)})")
    # 分 ofi 强度
    tiers = [(0.15, 0.3), (0.3, 0.5), (0.5, 1.01)]
    print("  |ofi| 分档(同向桶):")
    for lo_t, hi_t in tiers:
        sub = [x for x in agree if lo_t <= abs(x[1]) < hi_t]
        if sub:
            e = sum(x[3] for x in sub) / len(sub)
            print(f"    [{lo_t},{hi_t}) n={len(sub):>4} 期望 {e:+.2f}bp")
    # 90s 漂移分布(全部样本)→ TP/SL 建议
    if all_mo:
        srt = sorted(all_mo)
        n = len(srt)
        print(f"\n== 90s 漂移分布(n={n})== ")
        print(f"  p10={srt[int(n*0.1)]:+.1f} p25={srt[int(n*0.25)]:+.1f} "
              f"p50={srt[n//2]:+.1f} p75={srt[int(n*0.75)]:+.1f} p90={srt[int(n*0.9)]:+.1f}bp")
        print(f"  ⇒ TP 建议 ≈ +{srt[int(n*0.75)]:.0f}bp,SL 建议 ≈ −{abs(srt[int(n*0.25)]):.0f}bp")
        out = {"ts": time.time(), "n_samples": len(samples), "n_pulses": len(pulses),
               "agree_n": len(agree), "agree_exp90_bp": round(
                   (sum(mo for _, mo in agree_mo) / len(agree_mo)) if agree_mo else 0.0, 2),
               "p25_bp": round(srt[int(n*0.25)], 1), "p75_bp": round(srt[int(n*0.75)], 1),
               "p90_bp": round(srt[int(n*0.9)], 1)}
        (ROOT / "data" / "flow_event_study_last.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print("  ✓ 已写 data/flow_event_study_last.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
