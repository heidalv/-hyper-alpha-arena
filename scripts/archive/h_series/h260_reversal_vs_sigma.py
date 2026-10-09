# -*- coding: utf-8 -*-
"""H260 决定性：反转 alpha 是否随 σ（波动）增强 —— 决定 σ 闸去留。

# 背景

框架已从「双边做市」纠偏到「方向性短期反转」（counter_trend）。

H257 已证：逆势−顺势差随 |趋势| 幅度**单调增强**（+1.32 → +7.78bp）。

而 |趋势| 大 ⇔ 波动大 ⇔ σ 高。所以一个自然推论：

    **反转 alpha 在高波动时最强。**

若是，则 `vol_pause_sigma=0.7`（车道级 σ 闸，高波动时停整车道）
在反转框架下是**停掉了最赚钱的时刻** —— 这是做市时代遗留的、现在错掉的约束。
（观测证据：counter_trend 上线后 vol_pause 仍高达 1251 次/小时量级，主导车道行为。）

# 本脚本

用引擎口径 `realized_vol_bp(mid_hist, 20)` 给每条腿打 σ 标签，
按 σ 分档，看**逆势 vs 顺势**的 net_bp。

判据：
  · 若逆势−顺势差随 σ **单调增大** ⇒ σ 闸是束缚，应放开/大幅提高阈值
  · 若随 σ 减小 ⇒ σ 闸仍有保护作用，保留

# 用法

    python scripts/h260_reversal_vs_sigma.py --hours 12
"""
from __future__ import annotations

import argparse
import bisect
import json
import pathlib
import statistics as st
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h260_reversal_vs_sigma.json"
LANE = "mm_asterdex"
CUR = ["ASTERUSDT", "XRPUSDT", "SOLUSDT", "HYPEUSDT", "ADAUSDT"]
K = 120.0


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def market_dsn() -> str:
    return dsn().rsplit("/", 1)[0] + "/alpha_market"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=12.0)
    a = ap.parse_args()

    import psycopg
    from backend.services.market_maker.core import realized_vol_bp

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, ts, coalesce(meta_json->>'side',''),
                       coalesce(net_bp,0), coalesce(notional,0)
                FROM lane_ledger
                WHERE lane_id=%s AND (meta_json->'flatten')::text='false'
                  AND ts >= now() - (%s || ' hours')::interval
                  AND coalesce(notional,0) > 0
                ORDER BY ts ASC
            """, (LANE, str(float(a.hours))))
            legs = cur.fetchall()

    # 15s 网格 tick（引擎口径）
    G = {}
    for sym in CUR:
        try:
            with psycopg.connect(market_dsn()) as c:
                with c.cursor() as cur:
                    cur.execute("""
                        SELECT DISTINCT ON (bucket) bucket, bid_px, ask_px
                        FROM (SELECT (event_ts_ms/15000) AS bucket, bid_px, ask_px
                              FROM asterdex_book_ticker
                              WHERE ingest_ts >= now() - (%s || ' hours')::interval
                                AND symbol = %s AND bid_px>0 AND ask_px>bid_px) t
                        ORDER BY bucket, bid_px
                    """, (str(float(a.hours) + 0.3), sym))
                    G[sym] = {int(b): ((float(x) + float(y)) / 2.0)
                              for b, x, y in cur.fetchall()}
        except Exception as e:
            print(f"  ⚠️ {sym}: {str(e)[:60]}")

    print("=" * 104)
    print("H260  反转 alpha 是否随 σ 增强（决定 σ 闸去留）")
    print("=" * 104)

    rows = []
    for sym, ts, side, net, notl in legs:
        tk = str(sym).upper()
        if not tk.endswith("USDT"):
            tk += "USDT"
        d = G.get(tk)
        if not d:
            continue
        ks = sorted(d)
        t = int(ts.timestamp())
        b0 = t // 15
        # 成交前的 mid_hist（15s 网格）
        hist = [d[k] for k in ks if k <= b0][-21:]
        if len(hist) < 3:
            continue
        rv = realized_vol_bp(hist, 20)
        # 趋势（120s = 8 期）
        if len(hist) < 9:
            continue
        trend = (hist[-1] - hist[-9]) / hist[-9] * 1e4 if hist[-9] > 0 else 0.0
        up = trend > 0
        is_ct = (side == "buy" and not up) or (side == "sell" and up)
        rows.append({"rv": rv, "trend": trend, "net": float(net), "ct": is_ct,
                     "notional": float(notl)})

    print(f"\n  匹配 {len(rows)} 条腿")
    rvs = sorted(r["rv"] for r in rows)
    n = len(rvs)
    EDGES = [0.0] + [rvs[int(n * p)] for p in (0.25, 0.5, 0.75)] + [1e9]

    print(f"\n{'━'*104}\n  按 realized_vol_bp 分档：逆势 vs 顺势 net_bp\n{'━'*104}")
    print(f"\n  {'σ档(bp)':>14}{'腿数':>7}{'逆势net':>10}{'顺势net':>10}"
          f"{'差':>9}{'逆势为正%':>12}")
    for i in range(len(EDGES) - 1):
        lo, hi = EDGES[i], EDGES[i + 1]
        sel = [r for r in rows if lo <= r["rv"] < hi]
        ct = [r["net"] for r in sel if r["ct"]]
        mt = [r["net"] for r in sel if not r["ct"]]
        if len(sel) < 20:
            continue
        mct = st.mean(ct) if ct else 0.0
        mmt = st.mean(mt) if mt else 0.0
        pos = sum(1 for x in ct if x > 0) / len(ct) * 100 if ct else 0.0
        lbl = f"{lo:.2f}–{hi:.2f}" if hi < 1e9 else f"{lo:.2f}+"
        print(f"  {lbl:>14}{len(sel):>7}{mct:>+10.4f}{mmt:>+10.4f}"
              f"{mct-mmt:>+9.4f}{pos:>11.1f}%")

    # 结论
    print(f"\n{'━'*104}\n  结论\n{'━'*104}")
    diffs = []
    for i in range(len(EDGES) - 1):
        lo, hi = EDGES[i], EDGES[i + 1]
        sel = [r for r in rows if lo <= r["rv"] < hi]
        ct = [r["net"] for r in sel if r["ct"]]
        mt = [r["net"] for r in sel if not r["ct"]]
        if len(sel) >= 20 and ct and mt:
            diffs.append((lo, hi, st.mean(ct) - st.mean(mt), len(sel)))
    if len(diffs) >= 2:
        mono = all(diffs[i][2] <= diffs[i + 1][2] for i in range(len(diffs) - 1))
        print(f"\n  逆势−顺势差序列：{[round(d[2],2) for d in diffs]}")
        if mono:
            print(f"  ⇒ **单调增大** ⇒ σ 闸（高波动停）在反转框架下是**束缚**，")
            print(f"     应大幅提高 vol_pause_sigma 或关闭，让高波动时刻也做反转交易")
        else:
            print(f"  ⇒ 非单调 ⇒ σ 闸仍需保留（高波动不一定更好）")
        # 阈值含义：σ 闸在 rv > baseline*(1+0.7) 时停；当前 baseline≈3.9
        # 所以 σ 闸停的是 rv > 6.6 的时刻
        print(f"\n  当前 σ 闸：vol_pause_sigma=0.7，baseline≈3.9 ⇒ 停 rv > 6.6 的时刻")
        hi_sel = [r for r in rows if r["rv"] > 6.6]
        if hi_sel:
            ct = st.mean([r["net"] for r in hi_sel if r["ct"]]) if any(r["ct"] for r in hi_sel) else 0
            mt = st.mean([r["net"] for r in hi_sel if not r["ct"]]) if any(not r["ct"] for r in hi_sel) else 0
            print(f"  被 σ 闸停掉的时刻里：逆势 {ct:+.3f} / 顺势 {mt:+.3f} / 差 {ct-mt:+.3f}bp")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"hours": a.hours, "n": len(rows),
                               "diffs": [[round(d[0], 2), round(d[2], 3)] for d in diffs]},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
