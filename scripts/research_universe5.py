# -*- coding: utf-8 -*-
"""研究：① h356 摘除决策的逐币数据复核；② 5 币宇宙的敞口实况（峰值名义 vs 权益）。只读。"""
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

print("== ① 摘除决策复核：试跑窗口逐币净额（13:00 判定口径）==")
vd = ROOT / "research_l1" / "out" / "h356_verdict.json"
if vd.exists():
    j = json.loads(vd.read_text(encoding="utf-8"))
    for label, items in (("保留 keep", j.get("keep") or []),
                         ("摘除 drop", j.get("drop") or [])):
        print(f"\n-- {label} --")
        for s in items:
            print(f"  {s}")
else:
    print("  (无 verdict JSON——用账本重算)")
    cur.execute("""
        SELECT symbol, COUNT(*), SUM(net_bp)::int,
               ROUND(AVG(net_bp)::numeric,2)
        FROM lane_ledger
        WHERE event='fill' AND ts >= '2026-09-27T05:00:00+00'::timestamptz
          AND ts < '2026-09-28T05:00:00+00'::timestamptz
        GROUP BY 1 ORDER BY 3 DESC
    """)
    for r in cur.fetchall():
        print(f"  {r[0]:<6} n={r[1]:>5} Σnet={r[2]:>+8}bp 均={r[3]:>+7}bp")

print("\n== ② 5 币宇宙敞口实况（近 4h 各币峰值持仓名义 vs 权益）==")
st = ROOT / "logs" / "mm_lane_status.json"
jj = json.loads(st.read_text(encoding="utf-8")) if st.exists() else {}
eq = float(jj.get("equity") or 0.0)
print(f"  权益 = {eq:.2f}")
cur.execute("""
    SELECT symbol, MAX(qty*px) FROM (
        SELECT symbol,
               (meta_json->>'qty')::float8 AS qty,
               (meta_json->>'fill_px')::float8 AS px,
               meta_json->>'side' AS side,
               ts
        FROM lane_ledger WHERE event='fill' AND ts >= now() - interval '4 hours'
    ) t
    GROUP BY symbol ORDER BY 2 DESC
""")
# 上述 SQL 给的是单腿最大，不是持仓峰值；改用运行态快照
print("  （用运行态快照：当前持仓名义 vs 权益）")
states = jj.get("states") or {}
for sym, s in sorted(states.items()):
    q = float(s.get("qty") or 0.0)
    if abs(q) <= 1e-12:
        continue
    avg = float(s.get("avg_mid") or 0.0)
    notional = abs(q) * avg
    print(f"  {sym:<6} 名义=${notional:>7.2f}（权益的 {notional/max(eq,1)*100:.0f}%）")

print("\n== ③ 近 4h 单腿名义分布 ==")
cur.execute("""
    SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY notional)::int AS med,
           PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY notional)::int AS p90,
           MAX(notional)::int
    FROM lane_ledger WHERE event='fill' AND ts >= now() - interval '4 hours'
""")
r = cur.fetchone()
print(f"  中位=${r[0]}  p90=${r[1]}  最大=${r[2]}")
