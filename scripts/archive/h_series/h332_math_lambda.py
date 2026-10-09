# -*- coding: utf-8 -*-
"""H332 数学层：Kyle λ（订单流价格冲击）估计 + 盈亏分解的正式化。

# 模型
   Kyle(1985) 简化：Δmid_{t→t+H} ≈ λ_H · OFI_t + ε    （bp 对无量纲 OFI）
   三个时域 H ∈ {15s, 60s, 300s}、每币 OLS。λ 越大 = 流越"知情"。
   另测**成交条件化 λ**：E[markout | 我们的成交, OFI_t] 的斜率——即"当我们成交时，
   一单位流失衡对应多少 bp 的逆向漂移"，这是被动做市逆选择的直接参数。

# 盈亏分解（正式化，代入已测常数）
   每往返期望 = 2·s·q·p_fill（价差捕获） − q·E[adv|fill]（逆选择） − fees
   已知：s=0.0675bp、捕获/笔=0.047bp（六维账本 30 天）、adv≈0.11~0.33bp、
        fees≈0（被动出口改造后）
   ⇒ 盈亏平衡半价差 s* = E[adv|fill]/2·p_fill ≈ adv/2（p_fill≈1 时）
   结论预判：adv ≈ 0.2bp 需要 s* ≈ 0.1bp —— 与现行 0.0675bp 相差 ~50%，
   且加宽挂单会掉成交概率（h316：越远越差）⇒ 单纯价差路不通，必须降低 adv。

# 用法: python scripts/h332_math_lambda.py [--hours 168]
"""
from __future__ import annotations

import argparse
import bisect
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h332_lambda.json"
HORIZONS = [15, 60, 300]


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
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,DOGEUSDT")
    a = ap.parse_args()

    import numpy as np
    import psycopg

    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    out = {}
    print(f"{'币':<8} " + "".join(f"{'λ'+str(h)+'s':>10} {'R²':>6}   " for h in HORIZONS))
    for sym in syms:
        with psycopg.connect(read_env_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT (event_ts_ms/1000)::bigint AS b,
                           (array_agg(bid_px ORDER BY event_ts_ms DESC))[1] AS bid,
                           (array_agg(ask_px ORDER BY event_ts_ms DESC))[1] AS ask
                    FROM asterdex_book_ticker
                    WHERE symbol=%s AND event_ts_ms >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                      AND bid_px>0 AND ask_px>bid_px
                    GROUP BY b ORDER BY b
                """, (sym, a.hours))
                rows = cur.fetchall()
        ks = [int(r[0]) for r in rows]
        mids = [float(r[1] + r[2]) / 2.0 for r in rows]
        bare = sym[:-4] if sym.endswith("USDT") else sym
        with psycopg.connect(read_env_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT timestamp, COALESCE(taker_buy_notional,0), COALESCE(taker_sell_notional,0)
                    FROM market_trades_aggregated
                    WHERE symbol=%s AND timestamp >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                    ORDER BY timestamp
                """, (bare, a.hours))
                orows = cur.fetchall()
        ofi = {}
        for ts_ms, bn, sn in orows:
            tot = float(bn) + float(sn)
            if tot > 0:
                ofi[int(ts_ms) // 15000] = (float(bn) - float(sn)) / tot

        def mid_at(t):
            j = bisect.bisect_right(ks, t) - 1
            return mids[j] if j >= 0 and t - ks[j] <= 5 else None

        cells = []
        row = {"symbol": bare}
        for H in HORIZONS:
            xs, ys = [], []
            buckets = sorted(ofi)
            for b in buckets:
                t = b * 15
                m0 = mid_at(t)
                m1 = mid_at(t + H)
                if m0 is None or m1 is None or m0 <= 0:
                    continue
                xs.append(ofi[b])
                ys.append((m1 - m0) / m0 * 1e4)
            if len(xs) < 200:
                row[H] = None
                cells.append("".rjust(10) + "".rjust(6) + "   ")
                continue
            xs = np.array(xs)
            ys = np.array(ys)
            A = np.vstack([xs, np.ones(len(xs))]).T
            coef, *_ = np.linalg.lstsq(A, ys, rcond=None)
            resid = ys - A @ coef
            r2 = 1.0 - (resid @ resid) / ((ys - ys.mean()) @ (ys - ys.mean()))
            lam = float(coef[0])
            row[H] = {"lambda_bp": round(lam, 4), "r2": round(float(r2), 4), "n": len(xs)}
            cells.append(f"{lam:>+10.4f} {r2:>6.4f}   ")
        out[bare] = row
        print(f"{bare:<8} " + "".join(cells))

    OUT.write_text(json.dumps({"hours": a.hours, "lambda": out},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    print("\n分解校验（代入常数）：")
    print("  捕获/笔 0.047bp | 逆选择/笔 ≈0.11~0.33bp（30 天账本 + 48h markout）")
    print("  盈亏平衡半价差 s* = adv/2 ≈ 0.055~0.17bp；现行 s=0.0675bp 处于边缘")
    print("  结论：价差路径无解（加宽掉量，h316），必须把 adv（逆选择）本身降下来——")
    print("        方向 = 只做成交后漂移为负的环境（顺势回调子集 +5~8bp 即为此）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
