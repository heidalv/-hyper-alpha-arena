# -*- coding: utf-8 -*-
"""H330 多尺度动量/反转结构：1s 收益自相关 + 方差比（Lo-MacKinlay）。

# 目的（根基研究·动量数据层）
   前一轮信号筛查只测了"60s/300s/900s 收益对 60s 前向收益的相关"。
   这里做**完整的多尺度结构**：每个滞后 τ 的自相关 ρ(τ)（负=反转、正=动量）、
   方差比 VR(q)（<1 均值回归、>1 动量/长期记忆），找出每个币的
   "反转→动量交叉时域"，与文献的分界（秒级噪音反转 vs 分钟级动量）对照。

# 数学
   ρ(τ) = Cov(r_t, r_{t−τ}) / Var(r_t)，t 统计 ≈ ρ·√(N−τ)（近似）
   VR(q) = Var(r_q)/[q·Var(r_1)]，z 统计按 Lo-MacKinlay(1988) 同方差假设

# 用法: python scripts/h330_momentum_structure.py [--hours 168]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h330_momentum.json"
LAGS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900]
QS = [15, 60, 300, 900]


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=168.0)
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,DOGEUSDT,XRPUSDT")
    a = ap.parse_args()

    import numpy as np
    import psycopg

    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    out = {}
    print(f"{'币':<9} " + "".join(f"{'ρ(' + str(l) + 's)':>9}" for l in LAGS)
         + "".join(f"{'VR' + str(q):>9}" for q in QS))
    for sym in syms:
        with psycopg.connect(read_env_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ON (bucket) bucket, (bid_px+ask_px)/2.0 AS mid
                    FROM (SELECT (event_ts_ms/1000) AS bucket, bid_px, ask_px
                          FROM asterdex_book_ticker
                          WHERE event_ts_ms >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                            AND symbol = %s AND bid_px>0 AND ask_px>bid_px) t
                    ORDER BY bucket, mid
                """, (a.hours, sym))
                recs = cur.fetchall()
        mids = np.array([float(r[1]) for r in recs])
        n = len(mids)
        if n < 1000:
            print(f"{sym.replace('USDT',''):<9} 样本不足 {n}")
            continue
        r1 = np.diff(np.log(mids)) * 1e4          # 1s 收益(bp)
        N = len(r1)
        m = r1.mean()
        var = np.var(r1)
        row = {"n": int(N), "ac": {}, "vr": {}}
        cells = []
        for lag in LAGS:
            if lag >= N:
                cells.append("".rjust(9))
                continue
            x = r1[lag:]
            y = r1[:-lag]
            ac = ((x - m) * (y - m)).mean() / var if var > 0 else 0.0
            row["ac"][lag] = round(float(ac), 5)
            cells.append(f"{ac:>+9.4f}")
        for q in QS:
            k = N // q
            if k < 10:
                row["vr"][q] = None
                cells.append("".rjust(9))
                continue
            rq = r1[: k * q].reshape(k, q).sum(axis=1)
            vr = np.var(rq, ddof=1) / (q * np.var(r1[: k * q], ddof=1))
            row["vr"][q] = round(float(vr), 4)
            cells.append(f"{vr:>9.3f}")
        out[sym.replace("USDT", "")] = row
        print(f"{sym.replace('USDT',''):<9} " + "".join(cells))

    OUT.write_text(json.dumps({"hours": a.hours, "by_symbol": out},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    print("\n判读：ρ<0=反转（均值回归）、ρ>0=动量；VR<1=均值回归、VR>1=动量/长记忆。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
