# -*- coding: utf-8 -*-
"""H271 P1 换币观测：PENDLE 入宇宙后的逐币表现。

# 判据

  1. PENDLE 真的成交了吗（tick 率 0.92/s 低，可能成交少——这是最大风险）
  2. PENDLE 单腿 net_bp（H263 预测逆势 +1.128bp，差 +2.257bp）
  3. SOL/HYPE 是否继续转正（P2 改后 +0.20/+0.22bp）
  4. 无锁死、ok 恒真
  5. 与换币前 ASTER（−2.28bp 亏损主因）对比

# 用法

    python scripts/h271_p1_watch.py
    python scripts/h271_p1_watch.py --minutes 120 --interval 600
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"


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


def report(t0, t_start):
    import psycopg
    j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    el = max(1e-9, (time.time() - t_start) / 60.0)
    print(f"\n{'='*100}")
    print(f"  P1 换币观测  {dt.datetime.now():%H:%M:%S}　已 {el:.0f} 分钟")
    print(f"{'='*100}")
    print(f"  ok={j.get('ok')} ticks={j.get('ticks')} fills={j.get('fills')} "
          f"symbols={j.get('symbols')}")
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol,
                       count(*) FILTER (WHERE (meta_json->'flatten')::text='false'),
                       round(coalesce(sum(spread_bp*notional)/NULLIF(sum(notional),0),0)::numeric,4),
                       round(coalesce(sum(price_bp*notional)/NULLIF(sum(notional),0),0)::numeric,4),
                       round(coalesce(sum(net_bp*notional)/NULLIF(sum(notional),0),0)::numeric,4),
                       round(coalesce(sum(net_bp*notional/1e4),0)::numeric,3)
                FROM lane_ledger WHERE lane_id=%s AND ts >= %s
                  AND (meta_json->'flatten')::text='false'
                GROUP BY 1 ORDER BY 1
            """, (LANE, t0))
            rows = cur.fetchall()
    tot = 0.0
    print(f"\n  {'symbol':<10}{'maker腿':>8}{'spread':>9}{'price':>9}{'net_bp':>9}{'净额$':>10}{'单腿$':>10}")
    for s, mk, sp, px, net, usd in rows:
        mk = int(mk or 0)
        tot += float(usd or 0)
        per = float(usd or 0) / mk if mk else 0.0
        tag = " ←**新**" if s == "PENDLE" else ""
        print(f"  {s:<10}{mk:>8}{float(sp or 0):>+9.4f}{float(px or 0):>+9.4f}"
              f"{float(net or 0):>+9.4f}{float(usd or 0):>+10.3f}{per:>+10.4f}{tag}")
    print(f"\n  合计净额 ${tot:+.3f}")
    if not any(r[0] == "PENDLE" and r[1] > 0 for r in rows):
        print(f"  ⚠️ PENDLE 尚无成交 ⇒ 若 60 分钟后仍 0 腿，tick 率不足，需换候选")
    print(f"\n  参照：换币前 ASTER 30min −$1.50（−2.28bp，亏损主因）；"
          f"H263 PENDLE 逆势 +1.128bp")
    states = j.get("states") or {}
    for s, d in states.items():
        q = float(d.get("qty") or 0)
        if abs(q) > 1e-9:
            print(f"  持仓 {s}: {q:+.4f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=0.0)
    ap.add_argument("--interval", type=float, default=600.0)
    a = ap.parse_args()
    t0 = dt.datetime.now().astimezone()
    t_start = time.time()
    print("=" * 100)
    print("H271  P1 换币观测（ASTER → PENDLE）")
    print("=" * 100)
    if a.minutes <= 0:
        report(t0, t_start)
        return 0
    end = time.time() + a.minutes * 60
    while time.time() < end:
        report(t0, t_start)
        time.sleep(a.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
