# -*- coding: utf-8 -*-
"""账目核对 v2：纸面账户权威表 + 账本口径对齐 + 展示口径差异定位。只读。"""
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

print("== 纸面账户相关表 ==")
cur.execute("""
    SELECT table_name FROM information_schema.tables
    WHERE table_schema='public' AND (table_name LIKE '%paper%' OR table_name LIKE '%account%')
    ORDER BY 1
""")
tabs = [r[0] for r in cur.fetchall()]
print("  ", tabs)

for t in tabs:
    if "paper" in t or "account" in t:
        try:
            cur.execute(f"SELECT * FROM {t} LIMIT 3")
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            print(f"\n-- {t} 列：{cols}")
            for r in rows:
                print("   ", dict(zip(cols, r)))
        except Exception as e:
            print(f"\n-- {t} 读取失败：{e}")

print("\n== 账本本时代（07:27 起）==")
cur.execute("""
    SELECT COUNT(*), ROUND(SUM(net_bp*notional/1e4)::numeric,4),
           COUNT(*) FILTER (WHERE meta_json->>'exit_path' LIKE '%%taker%%'),
           ROUND(SUM(fee_bp*notional/1e4)::numeric,4)
    FROM lane_ledger
    WHERE event='fill' AND ts >= '2026-09-27T23:27:01+00'::timestamptz
""")
r = cur.fetchone()
print(f"  腿数={r[0]} Σ净额=${r[1]} taker={r[2]} Σ费=${r[3]}")

print("\n== 账本 02:40（v2）起 ==")
cur.execute("""
    SELECT COUNT(*), ROUND(SUM(net_bp*notional/1e4)::numeric,4),
           COUNT(*) FILTER (WHERE meta_json->>'exit_path' LIKE '%%taker%%')
    FROM lane_ledger
    WHERE event='fill' AND ts >= '2026-09-27T18:40:20+00'::timestamptz
""")
r = cur.fetchone()
print(f"  腿数={r[0]} Σ净额=${r[1]} taker={r[2]}")

print("\n== 账本最近 30 腿的 net_bp/notional 样例（核对口径）==")
cur.execute("""
    SELECT symbol, notional, net_bp, fee_bp, price_bp, spread_bp
    FROM lane_ledger WHERE event='fill' ORDER BY ts DESC LIMIT 8
""")
for r in cur.fetchall():
    print("  ", [str(x) for x in r])
