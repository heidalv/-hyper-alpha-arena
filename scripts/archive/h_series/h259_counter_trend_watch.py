# -*- coding: utf-8 -*-
"""H259 counter_trend 上线观测：是否真在只挂逆势侧 + 单腿是否转正。

# 验证点（按顺序）

1. **在起作用**：`skip_counts` 里出现 `ct_trend_up` / `ct_trend_down`
   （counter_trend 封顺势侧的标记；若一直为 0 ⇒ 分支没走到）
2. **报价单边化**：多数 tick 只有一侧报价（bid 或 ask 之一为 0）
3. **单腿转正**：切换后 maker 腿 net_bp 应显著高于切换前的 −0.9~−1.5bp，
   目标接近 H256 测的逆势 +0.84bp（lookback=120s）
4. **无锁死**：ok 恒真、无异常 lane_pause、库存能正常流转

# 用法

    python scripts/h259_counter_trend_watch.py
    python scripts/h259_counter_trend_watch.py --minutes 120 --interval 600
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
STATUS = ROOT / "logs" / "mm_lane_status.json"


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


def ledger_since(t0):
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT count(*) FILTER (WHERE (meta_json->'flatten')::text='false'),
                       count(*) FILTER (WHERE (meta_json->'flatten')::text='true'),
                       round(coalesce(sum(spread_bp*notional)/NULLIF(sum(notional),0),0)::numeric,4),
                       round(coalesce(sum(price_bp*notional)/NULLIF(sum(notional),0),0)::numeric,4),
                       round(coalesce(sum(net_bp*notional)/NULLIF(sum(notional),0),0)::numeric,4),
                       round(coalesce(sum(net_bp*notional/1e4),0)::numeric,3)
                FROM lane_ledger
                WHERE lane_id=%s AND ts >= %s AND (meta_json->'flatten')::text='false'
            """, (LANE, t0))
            mk, fl, sp, px_, net, usd = cur.fetchone()
    return {"mk": int(mk or 0), "fl": int(fl or 0), "spread": float(sp or 0),
            "price": float(px_ or 0), "net": float(net or 0), "usd": float(usd or 0)}


def report(t0, t_start):
    j = json.loads(STATUS.read_text(encoding="utf-8"))
    par = dict(j.get("params") or {})
    for k, v in dict(j.get("limits") or {}).items():
        par.setdefault(k, v)
    sk = dict(j.get("skip_counts") or {})
    el = max(1e-9, (time.time() - t_start) / 60.0)
    led = ledger_since(t0)
    print(f"\n{'='*100}")
    print(f"  counter_trend 观测  {dt.datetime.now():%H:%M:%S}　已 {el:.0f} 分钟")
    print(f"{'='*100}")
    print(f"  ok={j.get('ok')} ticks={j.get('ticks')} fills={j.get('fills')}")
    print(f"  side_mode={par.get('side_mode')} "
          f"lookback={par.get('trend_skew_lookback')} "
          f"min_bp={par.get('side_trend_min_bp')}")
    print(f"\n  ① 封顺势侧标记：")
    print(f"     ct_trend_up={sk.get('ct_trend_up', 0)}  "
          f"ct_trend_down={sk.get('ct_trend_down', 0)}"
          f"{'  ⇒ ✓ 在起作用' if sk.get('ct_trend_up', 0) + sk.get('ct_trend_down', 0) > 0 else '  ⇒ ⚠️ 尚未触发'}")
    print(f"\n  ② 报价单边化（计数含 0 的侧）：")
    for s, d in (j.get("states") or {}).items():
        b = d.get("quote_bid") or 0
        a = d.get("quote_ask") or 0
        one_side = "单边" if (b and not a) or (a and not b) else (
            "双边" if (b and a) else "无")
        print(f"     {s:8} qty={float(d.get('qty') or 0):>12.4f}  {one_side}")
    print(f"\n  ③ 切换后 maker 腿（累计，{el:.0f} 分钟）：")
    print(f"     腿数={led['mk']}  flat={led['fl']}  spread={led['spread']:+.4f}  "
          f"price={led['price']:+.4f}  **net={led['net']:+.4f}bp**  "
          f"净额=${led['usd']:+.3f}")
    print(f"     参照：切换前（both）net ≈ −0.9~−1.5bp；H256 逆势目标 ≈ +0.84bp")
    print(f"\n  ④ skip_counts 全量 = {json.dumps(sk, ensure_ascii=False)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=0.0)
    ap.add_argument("--interval", type=float, default=600.0)
    a = ap.parse_args()
    t0 = dt.datetime.now().astimezone()
    t_start = time.time()
    print("=" * 100)
    print("H259  counter_trend 上线观测")
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
