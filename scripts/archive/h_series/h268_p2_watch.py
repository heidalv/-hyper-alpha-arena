# -*- coding: utf-8 -*-
"""H268 P2 退出改造观测：counter_trend + 反转退出节奏的单腿是否转正。

# 观测点

  1. 单腿 net_bp（核心）：目标从 −0.54 → 转正（反转时平仓能吃到 +3~5bp）
  2. flat 腿（taker 平仓）频率：take_profit 5bp 会主动落袋，flat 会增加但每笔应是正的
  3. 持有期：max_one_side_seconds=120 后，持仓是否更快流转
  4. 无锁死：ok 恒真

# 用法

    python scripts/h268_p2_watch.py
    python scripts/h268_p2_watch.py --minutes 120 --interval 600
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
    par = dict(j.get("params") or {})
    for k, v in dict(j.get("limits") or {}).items():
        par.setdefault(k, v)
    el = max(1e-9, (time.time() - t_start) / 60.0)
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT count(*) FILTER (WHERE (meta_json->'flatten')::text='false'),
                       count(*) FILTER (WHERE (meta_json->'flatten')::text='true'),
                       round(coalesce(sum(spread_bp*notional)/NULLIF(sum(notional),0),0)::numeric,4),
                       round(coalesce(sum(price_bp*notional)/NULLIF(sum(notional),0),0)::numeric,4),
                       round(coalesce(sum(net_bp*notional)/NULLIF(sum(notional),0),0)::numeric,4),
                       round(coalesce(sum(net_bp*notional/1e4),0)::numeric,3)
                FROM lane_ledger WHERE lane_id=%s AND ts >= %s
                  AND (meta_json->'flatten')::text='false'
            """, (LANE, t0))
            mk, fl, sp, px, net, usd = cur.fetchone()
            cur.execute("""
                SELECT round(coalesce(sum(net_bp*notional/1e4),0)::numeric,3),
                       count(*)
                FROM lane_ledger WHERE lane_id=%s AND ts >= %s
                  AND (meta_json->'flatten')::text='true'
            """, (LANE, t0))
            fl_usd, fl_n = cur.fetchone()
    mk = int(mk or 0)
    print(f"\n{'='*100}")
    print(f"  P2 退出改造观测  {dt.datetime.now():%H:%M:%S}　已 {el:.0f} 分钟")
    print(f"{'='*100}")
    print(f"  ok={j.get('ok')} ticks={j.get('ticks')} fills={j.get('fills')}")
    print(f"  max_one_side_seconds={par.get('max_one_side_seconds')} "
          f"take_profit_bp={par.get('take_profit_bp')} "
          f"stop_loss_bp={par.get('stop_loss_bp')}")
    print(f"\n  maker 腿 {mk}　spread {float(sp):+.4f}　price {float(px):+.4f}　"
          f"**net {float(net):+.4f}bp**　净额 ${float(usd):+.3f}")
    if mk:
        print(f"  maker 单腿 ${float(usd)/mk:+.4f}")
    print(f"  flat 腿 {fl_n}　净额 ${float(fl_usd):+.3f}"
          f"{'（应为正，take_profit 落袋赢家）' if float(fl_usd)>0 else '（⚠️ 负，止损在砍）'}")
    print(f"\n  参照：P2 改前 counter_trend net ≈ −0.54bp；反转时平仓上界 +5.30bp")
    sk = j.get("skip_counts") or {}
    ct = sk.get("ct_trend_up", 0) + sk.get("ct_trend_down", 0)
    tp = sk.get("take_profit", 0)
    print(f"  counter_trend 封顺势 {ct} 次　take_profit 落袋 {tp} 次")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=0.0)
    ap.add_argument("--interval", type=float, default=600.0)
    a = ap.parse_args()
    t0 = dt.datetime.now().astimezone()
    t_start = time.time()
    print("=" * 100)
    print("H268  P2 退出改造观测")
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
