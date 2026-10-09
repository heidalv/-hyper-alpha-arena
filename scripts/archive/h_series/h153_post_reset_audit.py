# -*- coding: utf-8 -*-
"""[H153 2026-09-21] 重置后对账：哪些数字裁剪到了时代、哪些没有。

# 用户反馈

重置到 $300 后，面板显示：

    账户权益 $299.38   可用余额 $299.80   已实现 −$0.1858
    **手续费（累计） −$51.3854  taker 923 笔**      ← 可疑
    合计盈亏 −$0.6063（其中浮盈 −$0.4205）

权益/可用/已实现三个自洽（299.80 − 0.42 = 299.38 ✓，已实现 −0.1858 也是重置后的），
但手续费是 −$51.39 —— 那是**车道全历史**的量，明显没按重置时刻裁剪。

# 本脚本要回答

  1. 重置后的 `stats_since` 是什么？
  2. 各口径（账本已实现 / 手续费 / 成交笔数）在"全历史" vs "stats_since 之后"
     分别是多少？
  3. 面板上每一格**应该**用哪个口径？

用法：
    .venv\\Scripts\\python.exe scripts\\h153_post_reset_audit.py
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
    print("H153  重置后对账")
    print("=" * 96)

    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            meta = dict(cur.fetchone()[0] or {})
            since = meta.get("stats_since")
            print(f"\n  meta.stats_since = {since}")
            print(f"  meta.shadow_equity = {meta.get('shadow_equity')}")

            cur.execute("""SELECT total_equity, available_balance, frozen_balance,
                                  realized_pnl, updated_at
                           FROM arbitrage_paper_accounts WHERE id=%s""", (ACCT,))
            te, ab, fr, rp, upd = cur.fetchone()
            print(f"\n  账户 #{ACCT}（更新于 {upd}）")
            print(f"    total_equity(库)   {te}")
            print(f"    available_balance  {ab}")
            print(f"    frozen_balance     {fr}")
            print(f"    realized_pnl(库)   {rp}")

            print(f"\n  {'口径':<26} {'笔数':>7} {'净额$':>11} {'手续费$':>11} "
                  f"{'taker笔':>8}")
            print("  " + "-" * 68)
            for label, since_clause in (("全历史", None), ("stats_since 之后", since)):
                if since_clause is None:
                    cur.execute("""SELECT count(*), coalesce(sum(net_bp*notional/1e4),0),
                                          coalesce(sum(fee_bp*notional/1e4),0),
                                          count(*) FILTER (WHERE fee_bp < 0)
                                   FROM lane_ledger WHERE lane_id=%s AND event='fill'""",
                                (LANE,))
                else:
                    cur.execute("""SELECT count(*), coalesce(sum(net_bp*notional/1e4),0),
                                          coalesce(sum(fee_bp*notional/1e4),0),
                                          count(*) FILTER (WHERE fee_bp < 0)
                                   FROM lane_ledger WHERE lane_id=%s AND event='fill'
                                     AND ts >= %s""", (LANE, since_clause))
                n, net, fee, tk = cur.fetchone()
                print(f"  {label:<26} {int(n):>7} {float(net):>+11.4f} {float(fee):>+11.4f} "
                      f"{int(tk):>8}")
                if since_clause is None:
                    n_all, net_all, fee_all, tk_all = n, net, fee, tk
                else:
                    n_sc, net_sc, fee_sc, tk_sc = n, net, fee, tk

    print("\n" + "-" * 96)
    print("  应然口径（面板每一格该用哪个）：")
    print(f"    · 账本已实现  → **stats_since 之后**（重置后重新计）  = {float(rp):+.4f}（库里已是这个）")
    print(f"    · 手续费累计  → **stats_since 之后**（重置后重新计）  = {float(fee_sc):+.4f}")
    print(f"    · taker 笔数  → **stats_since 之后**                   = {int(tk_sc)}")
    print(f"    · 全历史（仅作审计，不该上主面板）                    = {float(fee_all):+.4f} / {int(tk_all)} 笔")
    print(f"\n  ⇒ 面板当前显示的 −$51.3854 / 923 笔 = **全历史**，与重置语义矛盾：")
    print(f"     重置把余额设回 $300、把统计时代清空，但手续费那格仍在数车道一生的账。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
