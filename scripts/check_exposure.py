# -*- coding: utf-8 -*-
"""持仓与敞口体检：当前持仓名义 vs 权益、近 1h 亏损腿解剖、市场跌幅对照。只读。"""
import sys
import json
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 1) 运行态持仓
st = ROOT / "logs" / "mm_lane_status.json"
j = json.loads(st.read_text(encoding="utf-8")) if st.exists() else {}
states = j.get("states") or {}
eq = j.get("equity")
print(f"== 当前持仓（equity={eq}）==")
tot_long = tot_short = 0.0
rows = []
for sym, s in sorted(states.items()):
    q = float(s.get("qty") or 0.0)
    if abs(q) <= 1e-12:
        continue
    avg = float(s.get("avg_mid") or 0.0)
    notional = abs(q) * avg
    rows.append((sym, q, avg, notional, float(s.get("opened_ts") or 0)))
    if q > 0:
        tot_long += notional
    else:
        tot_short += notional
print(f"  多头名义合计=${tot_long:.2f}  空头名义合计=${tot_short:.2f}  "
      f"总敞口=${tot_long + tot_short:.2f}  （权益倍数 {(tot_long+tot_short)/max(eq or 1,1):.1f}×）")
for sym, q, avg, notional, ts in rows:
    print(f"  {sym:<6} qty={q:>12.4f} avg={avg:<12.6g} 名义=${notional:>8.2f}"
          f"  （权益的 {notional/max(eq or 1,1)*100:.0f}%）")

# 2) 近 1h 亏损腿
c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()
print("\n== 近 1h 腿况 ==")
cur.execute("""
    SELECT COUNT(*), ROUND(SUM(net_bp*notional/1e4)::numeric,4),
           ROUND(AVG(net_bp)::numeric,2), ROUND(AVG(notional)::numeric,0),
           ROUND(MAX(notional)::numeric,0)
    FROM lane_ledger WHERE event='fill' AND ts >= now() - interval '1 hour'
""")
r = cur.fetchone()
print(f"  腿数={r[0]} Σ=${r[1]} 均净={r[2]}bp 均名义=${r[3]} 最大名义=${r[4]}")

print("\n== 近 1h 最亏 8 腿 ==")
cur.execute("""
    SELECT to_char(ts,'HH24:MI:SS'), symbol, meta_json->>'side',
           ROUND(notional::numeric,0), ROUND(net_bp::numeric,1),
           ROUND((net_bp*notional/1e4)::numeric,4),
           COALESCE(NULLIF(meta_json->>'exit_path',''),'(round)')
    FROM lane_ledger WHERE event='fill' AND ts >= now() - interval '1 hour'
    ORDER BY (net_bp*notional/1e4) ASC LIMIT 8
""")
for r in cur.fetchall():
    print(f"  {r[0]} {r[1]:<6} {r[2]:<5} 名义=${r[3]:>6} net={r[4]:>+7}bp "
          f"usd=${r[5]:>+8} {r[6]}")

# 3) 市场跌幅对照（BTC/ETH 5min 涨跌）
print("\n== 市场近 1h 波动（book ticker 中价）==")
c2 = psycopg.connect(read_env_dsn().replace('/alpha_arena', '/alpha_market'), autocommit=True)
cur2 = c2.cursor()
for sym in ("BTCUSDT", "ETHUSDT", "BNBUSDT"):
    cur2.execute("""
        SELECT (ARRAY_AGG((bid_px+ask_px)/2 ORDER BY event_ts_ms ASC))[1] AS first_p,
               (ARRAY_AGG((bid_px+ask_px)/2 ORDER BY event_ts_ms DESC))[1] AS last_p,
               MIN((bid_px+ask_px)/2), MAX((bid_px+ask_px)/2)
        FROM asterdex_book_ticker
        WHERE symbol=%s AND event_ts_ms >= (EXTRACT(EPOCH FROM now())*1000 - 3600*1000)::bigint
          AND bid_px>0 AND ask_px>bid_px
    """, (sym,))
    r = cur2.fetchone()
    if r and r[0]:
        chg = (float(r[1]) - float(r[0])) / float(r[0]) * 1e4
        rng = (float(r[3]) - float(r[2])) / float(r[2]) * 1e4
        print(f"  {sym:<9} 净变动={chg:>+7.1f}bp  区间幅度={rng:>6.1f}bp")
