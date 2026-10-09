# -*- coding: utf-8 -*-
"""P0 深查：vwap_revert_up / trend_down 两组的侧×出场路径×盈亏解剖（近 24h）。只读。"""
import sys
import pathlib
import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

c = psycopg.connect(read_env_dsn(), autocommit=True)
cur = c.cursor()

for reason in ("vwap_revert_up", "vwap_revert_down", "trend_down", "trend_up"):
    print(f"\n===== exit_reason={reason}（近 24h）=====")
    cur.execute("""
        SELECT meta_json->>'side', COALESCE(NULLIF(meta_json->>'exit_path',''),'(round)'),
               COUNT(*), ROUND(AVG(net_bp)::numeric,2),
               ROUND(SUM(net_bp*notional/1e4)::numeric,3),
               ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY net_bp)::numeric,1)
        FROM lane_ledger
        WHERE event='fill' AND ts >= now() - interval '24 hours'
          AND meta_json->>'exit_reason' = %s
        GROUP BY 1,2 ORDER BY 3 DESC LIMIT 8
    """, (reason,))
    for r in cur.fetchall():
        print(f"  {str(r[0]):<6} {str(r[1]):<22} n={r[2]:>5} 均={r[3]:>+7}bp "
              f"Σ=${r[4]:>+9} 中位={r[5]:>+6}bp")

# trend_down 的"离场时点 vs 之后走势"反事实：离场后 5min 价格继续跌还是反弹？
print("\n===== trend_down 离场反事实（离场后 5min 价格走向，抽样 60 腿）=====")
cur.execute("""
    SELECT symbol, ts, meta_json->>'side', (meta_json->>'mid_px')::float8
    FROM lane_ledger
    WHERE event='fill' AND ts >= now() - interval '24 hours'
      AND meta_json->>'exit_reason' = 'trend_down'
    ORDER BY ts DESC LIMIT 60
""")
rows = cur.fetchall()
c2 = psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market"),
                     autocommit=True)
cur2 = c2.cursor()
drift5 = []
for sym, ts, side, mid0 in rows:
    t0 = int(ts.timestamp() * 1000)
    cur2.execute("""
        SELECT (ARRAY_AGG((bid_px+ask_px)/2 ORDER BY event_ts_ms DESC))[1]
        FROM asterdex_book_ticker
        WHERE symbol=%s AND event_ts_ms BETWEEN %s AND %s
          AND bid_px>0 AND ask_px>bid_px
    """, (sym + "USDT", t0 + 300000, t0 + 310000))
    r = cur2.fetchone()
    if r and r[0] and mid0:
        d = (float(r[0]) - mid0) / mid0 * 1e4
        # side 是离场腿方向：sell 离场 = 平多 ⇒ 之后涨=亏、跌=对
        drift5.append((sym, side, round(d, 1)))
if drift5:
    n_rise = sum(1 for _, s, d in drift5 if (s == "sell" and d > 0) or (s == "buy" and d < 0))
    n_fall = sum(1 for _, s, d in drift5 if (s == "sell" and d < 0) or (s == "buy" and d > 0))
    print(f"  样本 {len(drift5)}：离场后 5min 继续不利={n_rise}，转有利={n_fall}，"
          f"持平={len(drift5)-n_rise-n_fall}")
    for sym, s, d in drift5[:20]:
        print(f"  {sym:<6} {s:<5} 5min后={d:>+6.1f}bp")
