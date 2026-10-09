# -*- coding: utf-8 -*-
"""H202 重置后的持仓与敞口画像 —— 新基准是否干净、风险有多大。

# 背景

2026-09-22 10:22:37 用户重置模拟账户资金。重置后：

  · 权益回到 **$300**（干净）
  · `stats_since` 同步重锚到 10:22:37
  · **但运行态仍有 4 个重置前开出的持仓**（账本最早 12:22–22:52 累积）

# 为什么这需要单独量化

重置**不会**平掉持仓，它只是把账户权益归零到起始本金。
于是出现一个容易忽略的状态：

    权益 $300（不含这些仓位的浮动盈亏）
    但账本里仍有 $1,7xx 名义的仓位敞口

后果分两面：

  · **对度量**：这些仓位的**入场**在旧时代、**出场**在新时代
    ⇒ 出场腿的 `net_bp` 会记进新账本。它的成本是真实的、属于新办法的，
      但它的**入场决策不是** ⇒ 归因时必须知道这一点，不能无脑算进新基准。
  · **对风险**：它们是真实的在险敞口。若净敞口接近 0（各币方向相互抵消），
    风险远小于"总名义"这个数字给人的印象。

本脚本把这两面都量出来。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"


def dsn(which: str = "alpha_arena") -> str:
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
    base, _, _ = url.rpartition("/")
    return f"{base}/{which}"


def main() -> int:
    import psycopg

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->>'stats_since' FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            since = (cur.fetchone() or [None])[0]
            cur.execute("""
                SELECT symbol,
                       sum(CASE WHEN meta_json->>'side'='buy'
                                THEN (meta_json->>'qty')::numeric
                                ELSE -(meta_json->>'qty')::numeric END) AS qty,
                       sum(CASE WHEN meta_json->>'side'='buy'
                                THEN (meta_json->>'qty')::numeric
                                     * (meta_json->>'fill_px')::numeric
                                ELSE -(meta_json->>'qty')::numeric
                                     * (meta_json->>'fill_px')::numeric END) AS cost_usd,
                       min(ts) AS t0, max(ts) AS t1
                FROM lane_ledger WHERE lane_id=%s
                GROUP BY symbol
                HAVING abs(sum(CASE WHEN meta_json->>'side'='buy'
                                    THEN (meta_json->>'qty')::numeric
                                    ELSE -(meta_json->>'qty')::numeric END)) > 1e-9
                ORDER BY symbol
            """, (LANE,))
            pos = cur.fetchall()

    if not pos:
        print("  （空仓）新基准干净")
        return 0

    print("=" * 92)
    print("H202  重置后的持仓与敞口画像")
    print("=" * 92)
    print(f"  stats_since = {since}")

    gross = 0.0
    net = 0.0
    rows = []
    with psycopg.connect(dsn("alpha_market")) as mc:
        with mc.cursor() as cur:
            for sym, qty, cost, t0, t1 in pos:
                cur.execute(
                    "SELECT (best_bid+best_ask)/2 FROM market_orderbook_snapshots"
                    " WHERE exchange='asterdex' AND symbol=%s"
                    " ORDER BY timestamp DESC LIMIT 1", (sym,))
                r = cur.fetchone()
                mid = float(r[0]) if r and r[0] else 0.0
                q = float(qty or 0)
                cst = float(cost or 0)
                avg_px = (cst / q) if abs(q) > 1e-12 else 0.0
                notl = abs(q) * mid
                upnl = (mid - avg_px) * q
                gross += notl
                net += q * mid
                rows.append((sym, q, avg_px, mid, notl, upnl, t0, t1))

    print(f"\n  {'币':<8}{'数量':>13}{'均价':>12}{'现价':>12}"
          f"{'名义$':>11}{'浮盈$':>10}   开仓时段")
    print("  " + "-" * 86)
    for sym, q, avg_px, mid, notl, upnl, t0, t1 in rows:
        print(f"  {sym:<8}{q:>13.3f}{avg_px:>12.6f}{mid:>12.6f}"
              f"{notl:>11,.0f}{upnl:>+10.3f}   {t0:%m-%d %H:%M} → {t1:%H:%M}")

    upnl_all = sum(r[5] for r in rows)
    print("  " + "-" * 86)
    print(f"  {'合计':<8}{'':>13}{'':>12}{'':>12}{gross:>11,.0f}{upnl_all:>+10.3f}")

    print(f"\n  总名义（gross）      ${gross:,.0f}")
    print(f"  净名义（net）        ${net:+,.0f}")
    print(f"  浮动盈亏合计         ${upnl_all:+.3f}")
    print(f"  账户权益             $300.00（重置值，**不含**上面这个浮亏）")

    eq = 300.0
    print(f"\n  敞口倍数：gross/equity = {gross/eq:.2f}x    |net|/equity = {abs(net)/eq:.3f}x")
    if abs(net) < 0.15 * gross:
        print("  ⇒ **净敞口很小（各币方向相互抵消）** ⇒ 方向性风险远小于 gross 给人的印象")
    else:
        print("  ⇒ 净敞口不小 ⇒ 存在真实的方向性风险，值得关注")

    print("\n  归因提示（重要）：")
    print("    · 这些仓位的**入场**发生在重置之前、**出场**会记进新账本")
    print("    · ⇒ 出场腿的成本是真实的新时代成本，但入场决策不是")
    print("    · ⇒ 分析新时代时，应把'这批遗留仓位的出场'单独标记，")
    print("      或等它们全部出库后再开始计时")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
