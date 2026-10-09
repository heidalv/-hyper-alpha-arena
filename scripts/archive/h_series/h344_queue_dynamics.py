# -*- coding: utf-8 -*-
"""H344 队列动态：贴盘口挂单的实证成交概率（深度快照 + 逐笔成交流）。

# 目的（根基研究·成交模型缺口）
   h284 的成交模型无法复现实盘成交动态（模拟顺势腿 markout≈0 vs 实盘 +5~8bp）。
   本脚本用我们自己的数据测三个基础量：
   ① 盘口顶端队列规模（前5档 qty，USD）——我们 300U 单腿占队列的份额；
   ② P(打穿)：给定快照，其后 Δ 秒内盘口被主动打穿（trade 价格 ≤ best_bid 或
      ≥ best_ask）的概率 —— 即"挂在贴盘口位置的限价单在 Δ 秒内成交"的实证估计；
   ③ 成交概率 vs Δ 的曲线（5s/15s/30s/60s）→ 直接给出 AS(2008) λ(δ) 的
      经验锚（替代文献假设的 κ≈20-40/bp）。
   数据：asterdex_depth_snapshots（5s 降采样）+ asterdex_trades（1s VWAP）。

# 用法: python scripts/h344_queue_dynamics.py [--hours 14]
"""
from __future__ import annotations

import argparse
import bisect
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h344_queue.json"
HORIZONS = [5, 15, 30, 60]


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
    ap.add_argument("--hours", type=float, default=14.0)
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,BNBUSDT")
    a = ap.parse_args()

    import psycopg

    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    out = {}
    with psycopg.connect(read_env_dsn()) as c:
        with c.cursor() as cur:
            for sym in syms:
                cur.execute("""
                    SELECT (event_ts_ms/1000)::bigint AS t,
                           (bids->0->>0)::float8 AS bb,
                           (asks->0->>0)::float8 AS ba,
                           COALESCE((bids->0->>1)::float8,0)+COALESCE((bids->1->>1)::float8,0)
                          +COALESCE((bids->2->>1)::float8,0)+COALESCE((bids->3->>1)::float8,0)
                          +COALESCE((bids->4->>1)::float8,0) AS bq5,
                           COALESCE((asks->0->>1)::float8,0)+COALESCE((asks->1->>1)::float8,0)
                          +COALESCE((asks->2->>1)::float8,0)+COALESCE((asks->3->>1)::float8,0)
                          +COALESCE((asks->4->>1)::float8,0) AS aq5
                    FROM asterdex_depth_snapshots
                    WHERE symbol=%s AND event_ts_ms >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                      AND bids IS NOT NULL AND asks IS NOT NULL
                    ORDER BY t
                """, (sym, a.hours))
                snaps = cur.fetchall()
                cur.execute("""
                    SELECT (event_ts_ms/1000)::bigint AS t, price, qty
                    FROM asterdex_trades
                    WHERE symbol=%s AND event_ts_ms >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                    ORDER BY t
                """, (sym, a.hours))
                trades = cur.fetchall()
                if len(snaps) < 50:
                    print(f"{sym}: 深度快照不足 {len(snaps)}")
                    continue
                ts = [r[0] for r in snaps]
                bq5 = [r[3] * (r[1] or 0) for r in snaps]
                aq5 = [r[4] * (r[2] or 0) for r in snaps]
                med_bq = sorted(bq5)[len(bq5) // 2]
                med_aq = sorted(aq5)[len(aq5) // 2]
                # 打穿检测：快照 t 的 best_bid/ask 被 (t, t+H] 内的成交打穿
                tt = [r[0] for r in trades]
                tpx = [r[1] for r in trades]
                rows = []
                for i, t in enumerate(ts):
                    bb, ba = snaps[i][1], snaps[i][2]
                    if not bb or not ba:
                        continue
                    hit = {h: False for h in HORIZONS}
                    j = bisect.bisect_right(tt, t)
                    for k in range(j, min(len(tt), j + 60)):
                        if tt[k] - t > 60:
                            break
                        if tt[k] <= t:
                            continue
                        for h in HORIZONS:
                            if tt[k] - t <= h and not hit[h]:
                                if tpx[k] <= bb or tpx[k] >= ba:
                                    hit[h] = True
                        if all(hit.values()):
                            break
                    rows.append(hit)
                n = len(rows)
                probs = {}
                for h in HORIZONS:
                    probs[h] = sum(1 for r in rows if r[h]) / n
                out[sym.replace("USDT", "")] = {"snaps": n, "med_bid5_usd": round(med_bq, 0),
                                                "med_ask5_usd": round(med_aq, 0),
                                                "p_fill": {str(h): round(probs[h], 4) for h in HORIZONS}}
                print(f"{sym.replace('USDT',''):<6} 快照{n:>5}  前5档买${med_bq:>8.0f}/卖${med_aq:>8.0f}  "
                      + "  ".join(f"P({h}s)={probs[h]:.3f}" for h in HORIZONS))

    OUT.write_text(json.dumps({"hours": a.hours, "by_symbol": out},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    print("\n判读：P(Δs) = 贴盘口限价单在 Δ 秒内被吃掉的实证概率（队列位置≈队尾的近似）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
