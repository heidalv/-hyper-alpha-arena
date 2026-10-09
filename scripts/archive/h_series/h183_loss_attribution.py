# -*- coding: utf-8 -*-
"""[H183 2026-09-21] 「亏损变大了」—— 立即归因。

# 要回答的

  1. 权益从峰值掉了多少？分几个阶段？
  2. 亏损来自哪个维度（价差/行情/费）？按币、按时段拆
  3. **哪些参数改动可能造成恶化**？逐个核对时间线
  4. 与"止损/止盈"各自的实际贡献

# 关键时间线（本轮所有改动）

    12:28:25  用户重置模拟账户 → $300
    13:39:53  worker 重启（timeout_exit_maker_only=True + spread_mult_reduce 0.95→0.4）
    14:00     止盈 take_profit_bp=12 上线
    14:56     A/B 第一轮启动
    15:03     worker 重启（加载 reduce_quote_disabled 字段）
    15:05     max_net_directional_ratio 6.0 → 4.0
    15:12     A/B 重启

用法：
    .venv\\Scripts\\python.exe scripts\\h183_loss_attribution.py
"""
from __future__ import annotations

import json
from datetime import datetime
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
    j = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
    eq = float(j.get("equity") or 0.0)
    leg = float(j.get("fill_notional") or 0.0)
    lim, par = j.get("limits") or {}, j.get("params") or {}

    print("=" * 98)
    print("H183  「亏损变大了」归因")
    print("=" * 98)
    print(f"  现在 {datetime.now().strftime('%H:%M:%S')}")
    print(f"  权益 **${eq:,.4f}**   单腿 ${leg:,.2f}")
    print(f"  相对起始 $300：**{eq-300:+.4f}**（{(eq-300)/300*100:+.2f}%）")
    print(f"  相对峰值 $306.53：**{eq-306.53:+.4f}**（{(eq-306.53)/306.53*100:+.2f}%）")

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->>'stats_since' FROM lane_registry"
                        " WHERE lane_id=%s", (LANE,))
            since = (cur.fetchone() or [None])[0]

            # 逐 10 分钟桶
            cur.execute("""
                SELECT to_char(date_trunc('hour', ts)
                         + interval '10 min' * floor(extract(minute from ts)/10),
                         'HH24:MI') AS b,
                       count(*),
                       round(sum(spread_bp*notional/1e4)::numeric,3),
                       round(sum(price_bp*notional/1e4)::numeric,3),
                       round(sum(fee_bp*notional/1e4)::numeric,3),
                       round(sum(net_bp*notional/1e4)::numeric,3),
                       round(sum(notional)::numeric,0)
                FROM lane_ledger WHERE lane_id=%s AND event='fill' AND ts >= %s
                GROUP BY 1 ORDER BY 1
            """, (LANE, since))
            buckets = cur.fetchall()

            # 按币
            cur.execute("""
                SELECT symbol, count(*),
                       round(sum(spread_bp*notional/1e4)::numeric,3),
                       round(sum(price_bp*notional/1e4)::numeric,3),
                       round(sum(fee_bp*notional/1e4)::numeric,3),
                       round(sum(net_bp*notional/1e4)::numeric,3)
                FROM lane_ledger WHERE lane_id=%s AND event='fill' AND ts >= %s
                GROUP BY 1 ORDER BY 6
            """, (LANE, since))
            bysym = cur.fetchall()

            # taker 腿
            cur.execute("""
                SELECT ts, symbol, coalesce(price_bp,0), coalesce(fee_bp,0),
                       coalesce(notional,0)
                FROM lane_ledger WHERE lane_id=%s AND event='fill' AND ts >= %s
                  AND coalesce(meta_json->>'flatten','false')='true'
                  AND coalesce(fee_bp,0) < 0 ORDER BY id
            """, (LANE, since))
            tks = cur.fetchall()

    print(f"\n  ── 逐 10 分钟桶（本时代，stats_since={str(since)[:19]}）──")
    print(f"  {'时段':<7} {'笔':>5} {'价差$':>9} {'行情$':>9} {'费$':>8} {'净$':>9} "
          f"{'名义$':>11}  累计净$")
    print("  " + "-" * 74)
    cum = 0.0
    for (b, n, sp, pr, fe, net, notl) in buckets:
        cum += float(net)
        print(f"  {b:<7} {n:>5} {float(sp):>+9.3f} {float(pr):>+9.3f} {float(fe):>+8.3f} "
              f"{float(net):>+9.3f} {float(notl):>11,.0f}  {cum:>+9.3f}")

    print(f"\n  ── 按币 ──")
    print(f"  {'币':<8} {'笔':>5} {'价差$':>9} {'行情$':>9} {'费$':>8} {'净$':>9}")
    print("  " + "-" * 54)
    for (s, n, sp, pr, fe, net) in bysym:
        print(f"  {s:<8} {n:>5} {float(sp):>+9.3f} {float(pr):>+9.3f} "
              f"{float(fe):>+8.3f} {float(net):>+9.3f}")

    print(f"\n  ── taker 腿（止损/止盈，共 {len(tks)} 笔）──")
    tot_tk = 0.0
    n_tp = n_sl = 0
    for (ts, sym, pbp, fbp, notl) in tks:
        net = float(notl) * (float(pbp) + float(fbp)) / 1e4
        tot_tk += net
        if float(pbp) > 0:
            n_tp += 1
        else:
            n_sl += 1
    print(f"    合计 **{tot_tk:+.4f} USD**   其中止盈 {n_tp} 笔 / 止损 {n_sl} 笔")

    print(f"\n  ── 判读 ──")
    print(f"    · 若「行情$」持续为负且量级 > 「价差$」⇒ 瓶颈是**持仓期行情**（做市的本质困难）")
    print(f"    · 若「费$」为大头 ⇒ taker 太多（止盈/止损过频）")
    print(f"    · 若某几个桶集中亏损 ⇒ 是**特定行情事件**，不是参数退化")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
