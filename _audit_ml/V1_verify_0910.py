# -*- coding: utf-8 -*-
"""V1 验证 9/9 中长线修复链生效情况（9/9 重启后 ~1.5 天真实数据，只读）。

验收标准（来自《中长线负期望根因报告》§23）：
  2. mid 中位持有 ≥ 48h；7. trend_broken 平仓均持有 ≥ 12h；6. 新开笔数降至原先 20-40%。
"""
import numpy as np
import psycopg

CONN = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"

def q(sql):
    with psycopg.connect(CONN, autocommit=True) as conn:
        conn.execute("set app.is_admin='on'")
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

print("== 1) 9/9 后新开仓（mid/long）==")
rows = q("""
    select symbol, side, timeframe_tier, trade_nature, strategy_id, opened_at, status
    from paper_positions
    where account_id=14 and opened_at >= '2026-09-09 00:00:00'
    order by opened_at
""")
print(f"总数 {len(rows)}")
for r in rows:
    print(f"  {str(r['opened_at'])[:16]} {r['symbol']:>8} {r['side']:>5} tier={r['timeframe_tier']} "
          f"{r['trade_nature']} {r['status']}")

print("\n== 2) 9/9 后平仓：按 close_reason × 持有时长 ==")
rows = q("""
    select close_reason, count(*) as n,
           round(avg(extract(epoch from (closed_at-opened_at))/3600)::numeric,1) as avg_h,
           round(sum(unrealized_pnl + partial_realized_pnl)::numeric,2) as pnl
    from paper_positions
    where account_id=14 and status='closed' and closed_at >= '2026-09-09 00:00:00'
      and timeframe_tier in ('mid','long')
    group by 1 order by pnl
""")
print(f"{'close_reason':<40} {'n':>4} {'avg_h':>7} {'pnl':>9}")
for r in rows:
    print(f"{(r['close_reason'] or '?')[:40]:<40} {r['n']:>4} {r['avg_h']:>7} {r['pnl']:>+9}")

print("\n== 3) trend_broken 逐笔持有时间（9/9 后）==")
rows = q("""
    select symbol, timeframe_tier, opened_at, closed_at,
           round(extract(epoch from (closed_at-opened_at))/3600)::int as hold_h,
           round((unrealized_pnl + partial_realized_pnl)::numeric,2) as pnl
    from paper_positions
    where account_id=14 and status='closed' and closed_at >= '2026-09-09 00:00:00'
      and close_reason like 'trend_broken%' and timeframe_tier in ('mid','long')
    order by closed_at
""")
for r in rows:
    print(f"  {str(r['opened_at'])[:16]}→{str(r['closed_at'])[:16]} {r['symbol']:>8} "
          f"tier={r['timeframe_tier']} hold={r['hold_h']}h pnl={r['pnl']:+}")

print("\n== 4) 9/9 后 mid/long 平仓的中位持有时间 ==")
rows = q("""
    select timeframe_tier, count(*) as n,
           percentile_cont(0.5) within group (order by extract(epoch from (closed_at-opened_at))/3600) as med_h
    from paper_positions
    where account_id=14 and status='closed' and closed_at >= '2026-09-09 00:00:00'
      and timeframe_tier in ('mid','long')
    group by 1
""")
for r in rows:
    print(f"  tier={r['timeframe_tier']} n={r['n']} median_hold={float(r['med_h']):.1f}h")

print("\n== 5) 当前 open 仓持有时长分布 ==")
rows = q("""
    select symbol, timeframe_tier, side, opened_at,
           round(extract(epoch from (now()-opened_at))/3600)::int as hold_h
    from paper_positions
    where account_id=14 and status='open' and timeframe_tier in ('mid','long')
    order by opened_at
""")
for r in rows:
    print(f"  {str(r['opened_at'])[:16]} {r['symbol']:>8} {r['side']:>5} tier={r['timeframe_tier']} hold={r['hold_h']}h")
