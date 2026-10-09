# -*- coding: utf-8 -*-
"""H129：最终状态核对 —— 宇宙 / 杠杆 / 止损 / 最近成交。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("DATABASE_URL", "")
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def main() -> int:
    st = Path("logs/mm_lane_status.json")
    j = json.loads(st.read_text(encoding="utf-8"))
    age = int((datetime.now() - datetime.fromtimestamp(st.stat().st_mtime)).total_seconds())
    lim = j.get("limits") or {}
    par = j.get("params") or {}
    eq = j.get("equity") or 0.0

    print("=" * 92)
    print("H129  最终状态核对")
    print("=" * 92)
    print(f"  现在 {datetime.now().strftime('%H:%M:%S')}    心跳年龄 {age}s   ok={j.get('ok')}")
    print(f"  宇宙       {j.get('symbols')}")
    print(f"  权益       {eq:.4f}      单腿 {j.get('fill_notional'):,.2f}")
    print(f"  杠杆       compound_ratio={par.get('compound_ratio')}")
    print(f"  止损       stop_loss_bp={lim.get('stop_loss_bp')}  "
          f"vol_min={lim.get('stop_loss_vol_min')}  (0=恒启用)")
    print(f"  持有窗口   max_one_side={lim.get('max_one_side_seconds')}s  "
          f"min_hold={lim.get('min_hold_seconds')}s")
    print(f"  每周期亏损上界 = {lim.get('stop_loss_bp', 0) * (j.get('fill_notional') or 0) / 1e4:.4f} USD")
    print(f"  拦截       {j.get('skip_counts')}")
    print(f"  速率       fills/h {j.get('fills_per_hour')}   ticks {j.get('ticks')}  "
          f"fills {j.get('fills')}  flattens {j.get('flattens')}")

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""SELECT meta_json->>'skip', count(*) FROM lane_ledger
                           WHERE lane_id='mm_asterdex' AND event='fill'
                           GROUP BY 1 ORDER BY 2 DESC""")
            print("\n  历史成交按 skip 归因：")
            for r in cur.fetchall():
                print(f"    {str(r[0]):<26} {r[1]:>6}")

            cur.execute("""
                SELECT ts, symbol, meta_json->>'side' AS side,
                       coalesce(price_bp,0), coalesce(spread_bp,0),
                       coalesce(fee_bp,0), coalesce(notional,0),
                       coalesce(meta_json->>'flatten','false')
                FROM lane_ledger
                WHERE lane_id='mm_asterdex' AND event='fill'
                  AND ts >= '2026-09-21 10:02:00+08'
                ORDER BY id DESC LIMIT 14
            """)
            rows = cur.fetchall()
            print(f"\n  10:02 之后最近 {len(rows)} 笔：")
            for (ts, sym, side, pbp, sbp, fbp, notl, flat) in rows:
                n = float(notl or 0)
                net = (float(pbp) + float(sbp) + float(fbp)) * n / 1e4
                print(f"    {ts.strftime('%H:%M:%S')} {str(sym):<7} {str(side):<5} "
                      f"flat={flat:<5} 名义 {n:>8,.0f}  price {float(pbp):>8.2f}bp  "
                      f"spread {float(sbp):>6.2f}bp  净 {net:>+8.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
