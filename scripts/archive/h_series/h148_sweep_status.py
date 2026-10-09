# -*- coding: utf-8 -*-
"""H148：查看宽度扫描（H146）的进度与各档结果。

用法：
    .venv\\Scripts\\python.exe scripts\\h148_sweep_status.py
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"


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
    env_txt = (ROOT / ".env").read_text(encoding="utf-8", errors="replace")
    target = ""
    for line in env_txt.splitlines():
        if line.startswith("MM_SPREAD_MULT="):
            target = line.split("=", 1)[1].strip()

    st = ROOT / "logs" / "mm_lane_status.json"
    j = json.loads(st.read_text(encoding="utf-8"))
    age = int((datetime.now() - datetime.fromtimestamp(st.stat().st_mtime)).total_seconds())
    prm = j.get("params") or {}

    print("=" * 96)
    print("H148  宽度扫描进度")
    print("=" * 96)
    print(f"  现在 {datetime.now().strftime('%H:%M:%S')}   心跳 {age}s")
    print(f"  .env 目标档 {target}   心跳实际 {prm.get('spread_mult')}"
          f"   {'✓ 一致' if str(prm.get('spread_mult')) == target else '（未生效/切换中）'}")
    print(f"  挂宽 bid/ask {j.get('avg_width_bp')}   base {j.get('avg_base_bp')}")
    print(f"  fills {j.get('fills')}  flattens {j.get('flattens')}  fills/h {j.get('fills_per_hour')}")
    print(f"  skip {j.get('skip_counts')}")

    print(f"\n  最近 25 分钟逐分钟（笔数 / 净 USD）")
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT date_trunc('minute', ts) AS m, count(*),
                       coalesce(sum(net_bp*notional/1e4),0)
                FROM lane_ledger
                WHERE lane_id=%s AND event='fill' AND ts >= now() - interval '25 minutes'
                GROUP BY 1 ORDER BY 1
            """, (LANE,))
            rows = cur.fetchall()
            tot_n = tot_usd = 0
            for m, n, net in rows:
                tot_n += int(n)
                tot_usd += float(net)
                bar = "#" * min(int(n), 40)
                print(f"    {m.strftime('%H:%M')}  {int(n):>4}  {float(net):>+9.4f}  {bar}")
            print(f"    {'合计':<6}  {tot_n:>4}  {tot_usd:>+9.4f}")

    out = ROOT / "research_l1" / "out" / "h146_width_sweep.json"
    if out.exists():
        print(f"\n  已完成的档位（{out.name}）：")
        d = json.loads(out.read_text(encoding="utf-8"))
        rs = d.get("results") or []
        if not rs:
            print("    （扫描尚未写入任何档位）")
        else:
            print(f"    {'spread_mult':>12} {'成交':>7} {'名义$':>13} {'价差bp':>9} "
                  f"{'行情bp':>9} {'净bp':>9} {'净$':>10}")
            for r in rs:
                print(f"    {r['spread_mult']:>12} {r['fills']:>7} {r['notional']:>13,.0f} "
                      f"{r['spread_bp_w']:>+9.4f} {r['price_bp_w']:>+9.4f} "
                      f"{r['net_bp_w']:>+9.4f} {r['net_usd']:>+10.4f}")
    else:
        print(f"\n  （{out.name} 尚未生成，扫描进行中）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
