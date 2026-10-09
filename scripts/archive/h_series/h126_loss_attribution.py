# -*- coding: utf-8 -*-
"""H126：09:50 之后的损失归因 + 宇宙热更新是否生效。"""
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


def main():
    print("=" * 104)
    print("H126  损失归因（09:45 之后）+ 宇宙状态")
    print("=" * 104)
    print(f"  现在 {datetime.now().strftime('%H:%M:%S')}")

    st = Path("logs/mm_lane_status.json")
    print(f"  心跳文件年龄 {int((datetime.now() - datetime.fromtimestamp(st.stat().st_mtime)).total_seconds())}s")
    j = json.loads(st.read_text(encoding="utf-8"))
    print(f"  ticks={j.get('ticks')} fills={j.get('fills')} flattens={j.get('flattens')} "
          f"equity={j.get('equity'):.4f}")
    print(f"  心跳 symbols = {j.get('symbols')}")
    print(f"  心跳 params.compound_ratio = {(j.get('params') or {}).get('compound_ratio')} "
          f"-> 单腿 {(j.get('params') or {}).get('compound_ratio', 0) * (j.get('equity') or 0):,.0f}")

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'symbols' FROM lane_registry WHERE lane_id='mm_asterdex'")
            print(f"  注册表 symbols = {cur.fetchone()[0]}")

            cur.execute("""
                SELECT id, ts, symbol,
                       meta_json->>'side'    AS side,
                       meta_json->>'flatten' AS flat,
                       coalesce(notional, 0)             AS notional,
                       coalesce(price_bp, 0)             AS price_bp,
                       coalesce(spread_bp, 0)            AS spread_bp,
                       coalesce(fee_bp, 0)               AS fee_bp,
                       coalesce(slippage_bp, 0)          AS slip_bp
                FROM lane_ledger
                WHERE lane_id='mm_asterdex' AND event='fill'
                  AND ts >= '2026-09-21 09:45:00+08'
                ORDER BY id
            """)
            rows = cur.fetchall()

    print(f"\n  09:45 之后成交 {len(rows)} 笔")
    print(f"  {'id':>6} {'时刻':<9} {'币':<8} {'side':<5} {'flat':<6} {'名义$':>9} "
          f"{'price_bp':>9} {'spread_bp':>9} {'fee_bp':>7} {'净$':>9}")
    print("  " + "-" * 96)
    tot = 0.0
    d_spread = d_price = d_fee = 0.0
    worst = []
    for (rid, ts, sym, side, flat, notl, pbp, sbp, fbp, slip) in rows:
        n = float(notl or 0.0)
        net_bp = float(sbp) + float(pbp) + float(fbp)
        usd = n * net_bp / 1e4
        tot += usd
        d_spread += n * float(sbp) / 1e4
        d_price += n * float(pbp) / 1e4
        d_fee += n * float(fbp) / 1e4
        worst.append((usd, rid, sym, ts, net_bp, n))
        print(f"  {rid:>6} {ts.strftime('%H:%M:%S'):<9} {str(sym):<8} {str(side):<5} "
              f"{str(flat):<6} {n:>9,.0f} {float(pbp):>9.2f} {float(sbp):>9.2f} "
              f"{float(fbp):>7.2f} {usd:>+9.4f}")

    print(f"\n  => 09:45 之后累计净额 **{tot:+.4f} USD**")
    worst.sort()
    print(f"\n  最差的 8 笔：")
    for usd, rid, sym, ts, net_bp, n in worst[:8]:
        print(f"    {ts.strftime('%H:%M:%S')} {sym:<8} 名义 ${n:>9,.0f}  net {net_bp:+9.2f}bp "
              f"=> **{usd:+.4f} USD**")

    print(f"\n  三维分解：")
    print(f"    价差(我们赚的)      {d_spread:+10.4f} USD")
    print(f"    行情漂移(我们赔的)  {d_price:+10.4f} USD")
    print(f"    手续费              {d_fee:+10.4f} USD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
