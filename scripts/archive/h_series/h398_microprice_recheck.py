# -*- coding: utf-8 -*-
"""H398 #9 证据复核：h365 口径微价研究重跑于 #2 判决后（当前）宇宙。

动机（h397 发现）：h365 的正向证据主体是 DOGE/SOL，而当前宇宙（#2 试跑）为
BNB/ETH/BTC/DOGE/SUI/NEAR/ARB/ADA/XRP/ENA，判决后预演剩 6 币——DOGE 是摘除候选、
SOL 不在宇宙 ⇒ mp_block_bp 上线前必须确认 mp_skew 正期望在当前宇宙仍成立。
口径与 h365 逐字一致（顶档微价、5s 网格、sign×Δmid、桶边际 + 分币种 f60s）。

用法: python scripts/h398_microprice_recheck.py [--hours 6] [--symbols ...]
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h398_microprice_cur.json"
HORIZONS = [1, 12, 24, 60]     # ×5s = 5/60/120/300s
SKEW_BUCKETS = [(0.25, 0.5, "s0.25-0.5"), (0.5, 1.0, "s0.5-1"), (1.0, 2.0, "s1-2"),
                (2.0, 5.0, "s2-5"), (5.0, 1e9, "s5+")]
DEFAULT_SYMS = "SUIUSDT,NEARUSDT,ARBUSDT,ADAUSDT,XRPUSDT,ENAUSDT"


def read_env_dsn() -> str:
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
    return url.replace("/alpha_arena", "/alpha_market")


def _stat(xs):
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    t = m / math.sqrt(var / n) if var > 0 else 0.0
    return {"n": n, "bp": round(m, 3), "t": round(t, 2)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--symbols", default=DEFAULT_SYMS)
    a = ap.parse_args()

    import psycopg

    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    all_rows = []

    for sym in syms:
        with psycopg.connect(read_env_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT (event_ts_ms/1000)::bigint AS t,
                           (bids->0->>0)::float8 AS bb,
                           (asks->0->>0)::float8 AS ba,
                           COALESCE((bids->0->>1)::float8,0) AS bq,
                           COALESCE((asks->0->>1)::float8,0) AS aq
                    FROM asterdex_depth_snapshots
                    WHERE symbol=%s AND event_ts_ms >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                      AND bids IS NOT NULL AND asks IS NOT NULL
                    ORDER BY t
                """, (sym, a.hours))
                snaps = cur.fetchall()
        if len(snaps) < 2000:
            print(f"{sym}: 深度快照不足 {len(snaps)}，跳过")
            continue
        bare = sym[:-4] if sym.endswith("USDT") else sym
        ts2, mids2, skews = [], [], []
        for r in snaps:
            bb, ba, bq, aq = r[1], r[2], r[3], r[4]
            if not (bb > 0 and ba > bb) or (bq + aq) <= 0:
                continue
            mid = (bb + ba) / 2.0
            mp = (bb * aq + ba * bq) / (bq + aq)
            skew = (mp - mid) / mid * 1e4
            ts2.append(r[0])
            mids2.append(mid)
            skews.append(skew)
        n = len(ts2)

        def fwd(i, h):
            j = i + h
            if j >= n or ts2[j] - ts2[i] < h * 5 * 0.7:
                return None
            return (mids2[j] - mids2[i]) / mids2[i] * 1e4

        n0 = len(all_rows)
        for i in range(n - max(HORIZONS)):
            sk = skews[i]
            if abs(sk) < 0.25:
                continue
            fws = {h: fwd(i, h) for h in HORIZONS}
            if not all(fws[h] is not None for h in HORIZONS):
                continue
            sign = 1.0 if sk > 0 else -1.0
            all_rows.append({"sym": bare, "skew": abs(sk),
                             **{f"f{h}": fws[h] * sign for h in HORIZONS}})
        print(f"{bare}: +{len(all_rows) - n0} 事件（快照 {n}）")

    print(f"\n总事件 {len(all_rows)}")
    if not all_rows:
        return 1

    print(f"\n── 边际 E[Δmid|skew]（sign×bp，sign=微价方向）──")
    print(f"{'桶':<12} {'n':>7} " + "".join(f"{f'f{h*5}s':>14}" for h in HORIZONS))
    out = {}
    for lo, hi, name in SKEW_BUCKETS:
        xs = {h: [r[f"f{h}"] for r in all_rows if lo <= r["skew"] < hi] for h in HORIZONS}
        rec = {"n": len(xs[HORIZONS[0]])}
        cells = []
        for h in HORIZONS:
            st = _stat(xs[h])
            if st:
                cells.append(f"{st['bp']:>+8.3f}({st['t']:+.1f})")
                rec[f"f{h}"] = st
        out[name] = rec
        if cells:
            print(f"{name:<12} {rec['n']:>7} " + "".join(cells))

    print("\n── 分币种（f12=60s）──")
    by_sym = {}
    for sym in sorted({r["sym"] for r in all_rows}):
        xs = [r["f12"] for r in all_rows if r["sym"] == sym]
        st = _stat(xs) if xs else None
        by_sym[sym] = st
        print(f"  {sym:<8} n={len(xs):>6}  f60s={st['bp']:>+8.3f}bp(t={st['t']:+.1f})"
              if st else f"  {sym}: 无")

    OUT.write_text(json.dumps({"hours": a.hours, "symbols": syms,
                               "n_total": len(all_rows), "marginal": out,
                               "by_symbol": by_sym}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
