# -*- coding: utf-8 -*-
"""[H145 2026-09-21] 结清「可用余额 vs 账本」的 $23 差额。

# 现场

  · 前端/接口 `available_balance` = 187.91
  · `realized_usd`（运行态口径）  = −88.98   ⇒ 300 − 88.98 = 211.02
  · `lane_ledger` 全历史净额      = −259.30  ⇒ 300 − 259.30 = **40.70**
  ⇒ 三个口径互不相等，差额必须先说清楚，否则任何"盈亏"读数都不可信。

# 已知的两个时代切分（必须先排除，否则会把"时代"误当"误差"）

  1. **账本时代**：`meta.stats_since` 是口径起点；2026-09-20 18:04 用户在前端
     重置过模拟账户（`old_stats_since` 记录在 `ops_changes`）⇒ `lane_ledger`
     里**跨时代**的行不能直接和当前账户余额比。
  2. **运行态时代**：`realized_pnl` 来自运行态成交，也可能有自己的起点。

# 判据

  · 若 `available_balance` == 300 +（stats_since 之后的账本净额）⇒ 完全自洽
  · 若不等 ⇒ 差额必须被某个**已知**的账外项解释（手续费重复计 / 资金费 /
    重置残留），否则就是真 bug，要写进文档而不是含糊过去

用法：
    .venv\\Scripts\\python.exe scripts\\h145_reconcile_gap.py
"""
from __future__ import annotations

import json
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
ACCT = 101


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
    print("=" * 96)
    print("H145  可用余额 vs 账本：差额结清")
    print("=" * 96)

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json, edge_json FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            meta_raw, edge_raw = cur.fetchone()
            meta = dict(meta_raw or {})

            stats_since = meta.get("stats_since")
            print(f"\n  meta.stats_since = {stats_since}")
            print(f"  meta.shadow_equity = {meta.get('shadow_equity')}")

            # 账户行
            cur.execute("""SELECT total_equity, available_balance, frozen_balance,
                                  realized_pnl, updated_at
                           FROM arbitrage_paper_accounts WHERE id=%s""", (ACCT,))
            r = cur.fetchone()
            te_db, ab, frozen, rp_db, upd = r
            print(f"\n  账户 #{ACCT}:")
            print(f"    total_equity(库)   {te_db}")
            print(f"    available_balance  **{ab}**")
            print(f"    frozen_balance     {frozen}")
            print(f"    realized_pnl(库)   {rp_db}")
            print(f"    updated_at         {upd}")

            # 账本：分时代
            for label, since in (("全历史", None), (f"stats_since 之后", stats_since)):
                if since is None:
                    cur.execute("""SELECT count(*), coalesce(sum(net_bp*notional/1e4),0),
                                          coalesce(sum(fee_bp*notional/1e4),0)
                                   FROM lane_ledger WHERE lane_id=%s AND event='fill'""",
                                (LANE,))
                else:
                    cur.execute("""SELECT count(*), coalesce(sum(net_bp*notional/1e4),0),
                                          coalesce(sum(fee_bp*notional/1e4),0)
                                   FROM lane_ledger WHERE lane_id=%s AND event='fill'
                                     AND ts >= %s""", (LANE, since))
                n, net, fee = cur.fetchone()
                net, fee = float(net or 0), float(fee or 0)
                print(f"\n  账本【{label}】: {n} 笔")
                print(f"    净额 {net:+.4f}   其中手续费 {fee:+.4f}")
                print(f"    ⇒ 300 + 净额 = **{300 + net:.4f}**")

            # 另一本账（统一模拟账户总账）
            cur.execute("""SELECT count(*), coalesce(sum(amount_usd),0)
                           FROM arbitrage_paper_ledgers WHERE account_id=%s""", (ACCT,))
            n2, amt2 = cur.fetchone()
            print(f"\n  统一总账 arbitrage_paper_ledgers: {n2} 行, Σamount_usd = {float(amt2 or 0):+.4f}")

    print("\n" + "-" * 96)
    print("  差额结清：")
    if ab is not None:
        print(f"    available_balance                        {float(ab):>12.4f}")
        if stats_since:
            print(f"    （对比上面『stats_since 之后』的 300+净额）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
