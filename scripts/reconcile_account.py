# -*- coding: utf-8 -*-
"""账目核对：账本累计净额 vs 权益轨迹 vs 展示口径（子权益/本已实现/本时代）。

展示口径来自 GUI：子权益（注册表快照）$300、本已实现 −$16.7116、手续费(本时代) −$2.2954、
名义资产 $264.77、合计盈亏 −$17.0651。本脚本用账本与注册表逐项对账。
只读。
"""
import sys
import json
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()

print("== 1) 注册表口径 ==")
cur.execute("SELECT meta_json, updated_at FROM lane_registry WHERE lane_id='mm_asterdex'")
m, upd = cur.fetchone()
print("  updated_at:", upd)
for k in ("equity", "sub_equity", "stats_since", "era", "start_equity",
          "paper_account_id", "params"):
    v = m.get(k)
    if k == "params":
        continue
    print(f"  {k}: {json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v}")

print("\n== 2) 账本累计（全历史）==")
cur.execute("""
    SELECT COUNT(*),
           ROUND(SUM(net_bp*notional/1e4)::numeric, 4),
           ROUND(SUM(fee_bp*notional/1e4)::numeric, 4),
           ROUND(SUM(price_bp*notional/1e4)::numeric, 4),
           ROUND(SUM(spread_bp*notional/1e4)::numeric, 4),
           COUNT(*) FILTER (WHERE meta_json->>'exit_path' LIKE '%taker%')
    FROM lane_ledger WHERE event='fill'
""")
r = cur.fetchone()
print(f"  腿数={r[0]}  Σ净额=${r[1]}  Σ费=${r[2]}  Σ价格=${r[3]}  Σ价差=${r[4]}  "
      f"taker 腿={r[5]}")
ledger_total = float(r[1] or 0)

print("\n== 3) 本时代（stats_since 起）==")
ss = m.get("stats_since")
if ss:
    cur.execute("""
        SELECT COUNT(*), ROUND(SUM(net_bp*notional/1e4)::numeric, 4),
               COUNT(*) FILTER (WHERE meta_json->>'exit_path' LIKE '%taker%')
        FROM lane_ledger WHERE event='fill' AND ts >= %s::timestamptz
    """, (ss,))
    r2 = cur.fetchone()
    print(f"  stats_since={ss[:19]}  腿数={r2[0]}  Σ净额=${r2[1]}  taker={r2[2]}")
    print(f"  （GUI 展示：本已实现 −16.7116 / 手续费 −2.2954 / taker 29 笔）")

print("\n== 4) 权益轨迹（逐 2h 累计净额，自 stats 起点前推）==")
cur.execute("""
    SELECT date_trunc('hour', ts) + (EXTRACT(MINUTE FROM ts)::int/120)*interval '2 hour' AS b,
           COUNT(*), ROUND(SUM(net_bp*notional/1e4)::numeric,2)
    FROM lane_ledger WHERE event='fill' AND ts >= now() - interval '24 hours'
    GROUP BY 1 ORDER BY 1
""")
tot = 0.0
for b, n, s in cur.fetchall():
    tot += float(s or 0)
    print(f"  {b:%m-%d %H:%M}  n={n:>4}  Σusd={s:>+8}  累计={tot:>+9.2f}")

print("\n== 5) 对账 ==")
print(f"  账本累计净额（全历史）      = {ledger_total:+.2f} USD")
print(f"  账本累计（近 24h）          = {tot:+.2f} USD")
print(f"  权益实际变化 300 → 264.77   = -35.23 USD")
print(f"  差额（未由账本净额解释的）  = {(-35.23 - ledger_total):+.2f} USD")
