# -*- coding: utf-8 -*-
"""H252 换币实验观测：ADA（宽价差 4.05bp）的实盘每腿 P&L vs 当前四币。

# 要回答的问题

离线模型（H247 校准到 3.8%、H251 逐档）说：

    ADA 半价差 P50 4.05bp、>1.3bp 占比 **100%**
    ⇒ Δ = 1.0×半价差（挂到盘口最优价）⇒ 预测 **+$0.0487/腿**

而当前四币（半价差 0.43~0.69bp）实测 **−$0.0368/腿**。

**但有一个模型无法回答的疑点**：ADA 的 tick 率只有 **0.64/s**，
而 ASTER 是 **12.86/s**（差 20 倍）。成交次数大致正比于 tick 率
⇒ 单腿转正但成交量可能塌 ⇒ **绝对收益反而可能更差**。

⇒ 只有实盘能回答。本脚本就是那个读数。

# 判据（按顺序，前一条不成立则后面不看）

1. **ADA 真的成交了吗**（腿数 > 0）？若几小时 0 腿 ⇒ "没成交量"，实验无意义
2. **ADA 的单腿 net_bp 是多少**？与模型预测（+$0.0487/腿 ÷ $250 ≈ +1.95bp）比
3. **ADA 的绝对净额** vs 同窗口当前四币的绝对净额（这才是决策依据）
4. 当前四币有没有被拖累（宇宙变大 ⇒ 敞口竞争）

# 用法

    python scripts/h252_f251_watch.py                # 单次
    python scripts/h252_f251_watch.py --minutes 180 --interval 600
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
NEW = "ADA"
OLD = ["ASTER", "SOL", "XRP", "HYPE"]


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


def window(t0, t1) -> dict:
    import psycopg
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol,
                       count(*) FILTER (WHERE (meta_json->'flatten')::text='false'),
                       count(*) FILTER (WHERE (meta_json->'flatten')::text='true'),
                       coalesce(sum(notional), 0),
                       coalesce(sum(spread_bp*notional)
                                / NULLIF(sum(notional),0), 0),
                       coalesce(sum(price_bp*notional)
                                / NULLIF(sum(notional),0), 0),
                       coalesce(sum(net_bp*notional)
                                / NULLIF(sum(notional),0), 0),
                       coalesce(sum(net_bp*notional/1e4), 0)
                FROM lane_ledger
                WHERE lane_id=%s AND ts >= %s AND ts < %s
                GROUP BY 1 ORDER BY 1
            """, (LANE, t0, t1))
            return {str(r[0]): {"mk": int(r[1] or 0), "fl": int(r[2] or 0),
                                "notional": float(r[3] or 0),
                                "spread_bp": float(r[4] or 0),
                                "price_bp": float(r[5] or 0),
                                "net_bp": float(r[6] or 0),
                                "net_usd": float(r[7] or 0)}
                    for r in cur.fetchall()}


def report(t0, t_start) -> None:
    t1 = dt.datetime.now().astimezone()
    el = max(1e-9, (time.time() - t_start) / 3600.0)
    w = window(t0, t1)
    print(f"\n{'='*100}")
    print(f"  F251 换币实验观测  {t1:%H:%M:%S}　已观测 {el*60:.0f} 分钟")
    print(f"{'='*100}")
    print(f"\n  {'symbol':<10}{'maker腿':>9}{'flat腿':>8}{'名义$':>12}"
          f"{'spread':>9}{'price':>9}{'net_bp':>9}{'净额$':>10}{'单腿$':>10}")
    agg_new = agg_old = 0.0
    for s in [NEW] + OLD:
        d = w.get(s)
        if not d:
            print(f"  {s:<10}{'（无腿）':>9}")
            continue
        per = d["net_usd"] / d["mk"] if d["mk"] else 0.0
        tag = " ←**新**" if s == NEW else ""
        print(f"  {s:<10}{d['mk']:>9}{d['fl']:>8}{d['notional']:>12,.0f}"
              f"{d['spread_bp']:>+9.4f}{d['price_bp']:>+9.4f}"
              f"{d['net_bp']:>+9.4f}{d['net_usd']:>+10.3f}{per:>+10.4f}{tag}")
        if s == NEW:
            agg_new += d["net_usd"]
        else:
            agg_old += d["net_usd"]
    tot = agg_new + agg_old
    print(f"\n  ⇒ **ADA 净额 ${agg_new:+.3f}**　当前四币 ${agg_old:+.3f}　合计 ${tot:+.3f}")
    a = w.get(NEW)
    if not a or a["mk"] == 0:
        print(f"  ⚠️ ADA 尚无成交 ⇒ 要么还没开始挂单，要么**没有成交量**")
        print(f"     若 60 分钟后仍 0 腿 ⇒ 这个标的流动性不足以做市（实验结论已明确）")
    else:
        print(f"\n  ADA 读数 vs 模型预测：")
        print(f"     模型预测（Δ=1×半价差，+$0.0487/腿）⇒ "
              f"{0.0487/250*1e4:+.3f} bp（按 $250 名义折算）")
        print(f"     实盘实测 单腿 ${a['net_usd']/a['mk']:+.4f}　"
              f"net_bp {a['net_bp']:+.3f}")
        print(f"     ⇒ {'**同号（模型成立）**' if (a['net_usd']>0) else '**符号相反（模型被证伪）**'}")
    # 与换币前基线对比
    print(f"\n  参照：换币前（今天 10:22 起）当前四币整体 = "
          f"约 **−$0.0368/腿**（1,340 腿口径）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=0.0)
    ap.add_argument("--interval", type=float, default=600.0)
    a = ap.parse_args()
    t0 = dt.datetime.now().astimezone()
    t_start = time.time()
    print("=" * 100)
    print("H252  F251 换币实验观测（ADA vs 当前四币）")
    print("=" * 100)
    print(f"  起点 {t0:%H:%M:%S}　目标宇宙 = ASTER,SOL,XRP,HYPE,ADA")
    if a.minutes <= 0:
        report(t0, t_start)
        return 0
    end = time.time() + a.minutes * 60
    while time.time() < end:
        report(t0, t_start)
        time.sleep(a.interval)
    report(t0, t_start)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
