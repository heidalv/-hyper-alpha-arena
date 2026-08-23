# -*- coding: utf-8 -*-
"""诊断 v7：scalp 赔率与胜率结构（修正列名）"""
import psycopg

conn = psycopg.connect('postgresql://laobao:alpha_pass@localhost:5432/alpha_arena')
conn.autocommit = False
cur = conn.cursor()
cur.execute("SET LOCAL app.tenant_id = '326'")
try:
    cur.execute("SET LOCAL app.is_admin = 'on'")
except Exception:
    pass

def q(sql, args=()):
    cur.execute(sql, args)
    return cur.fetchall()

print("[A] tp_level_reached 分布（scalp 平仓）")
for r in q("""
SELECT COALESCE(tp_level_reached,0), count(*) FROM paper_positions
WHERE trade_nature='scalp' AND status='closed' GROUP BY 1 ORDER BY 1"""):
    print("   ", r)

print("[B] 各 close_reason 的 entry→SL 距离 vs entry→TP 距离 vs 实际平仓价差（近14天 scalp）")
for r in q("""
SELECT COALESCE(close_reason,'?'), count(*),
  round(avg(100.0*ABS(entry_price-sl_price)/entry_price)::numeric,2) sl_dist,
  round(avg(100.0*ABS(tp_price-entry_price)/entry_price)::numeric,2) tp_dist,
  round(avg(100.0*ABS(close_price-entry_price)/entry_price)::numeric,2) close_move
FROM paper_positions
WHERE trade_nature='scalp' AND status='closed' AND closed_at >= now()-interval '14 days'
GROUP BY 1 ORDER BY 2 DESC LIMIT 14"""):
    print("   ", r)

print("[C] 盈亏率：pnl/保证金 按 close_reason（近14天 scalp, 逐仓）")
for r in q("""
SELECT COALESCE(close_reason,'?'), count(*),
  round(avg((partial_realized_pnl+unrealized_pnl)/NULLIF(margin,0)*100)::numeric,2) pnl_on_margin_pct
FROM paper_positions
WHERE trade_nature='scalp' AND status='closed' AND closed_at >= now()-interval '14 days'
GROUP BY 1 ORDER BY 2 DESC LIMIT 14"""):
    print("   ", r)

print("[D] 信号日志 horizon_sec 分布（近7天）")
for r in q("""
SELECT COALESCE(horizon_sec,0), count(*),
  round(100.0*sum(CASE WHEN win THEN 1 ELSE 0 END)/count(*),1) wr,
  round(sum(net_ret)::numeric,2)
FROM scalp_signal_log WHERE settled=true
  AND signal_ts >= extract(epoch from now() - interval '7 days')
GROUP BY 1 ORDER BY 2 DESC LIMIT 12"""):
    print("   ", r)

print("[E] 信号 net_ret 分布（近7天 settled）")
for r in q("""
SELECT width_bucket(net_ret*100, -5, 5, 10) b, count(*),
  round(avg(net_ret*100)::numeric,3)
FROM scalp_signal_log WHERE settled=true
  AND signal_ts >= extract(epoch from now() - interval '7 days')
GROUP BY 1 ORDER BY 1"""):
    print("   ", r)

print("[F] 仓位持有时长 vs 盈亏（近14天 scalp 平仓）")
for r in q("""
SELECT width_bucket(EXTRACT(EPOCH FROM (closed_at-opened_at))/3600.0, 0, 24, 12) b, count(*),
  round(avg(partial_realized_pnl+unrealized_pnl)::numeric,3),
  round(100.0*sum(CASE WHEN partial_realized_pnl+unrealized_pnl>0 THEN 1 ELSE 0 END)/count(*),1) wr
FROM paper_positions WHERE trade_nature='scalp' AND status='closed'
  AND closed_at >= now()-interval '14 days'
GROUP BY 1 ORDER BY 1"""):
    print("   ", r)

print("[G] 逐仓杠杆与单笔盈亏率分布（近14天 scalp）")
for r in q("""
SELECT leverage, count(*),
  round(avg((partial_realized_pnl+unrealized_pnl)/NULLIF(margin,0)*100)::numeric,2) pnl_margin_pct,
  round(100.0*sum(CASE WHEN partial_realized_pnl+unrealized_pnl>0 THEN 1 ELSE 0 END)/count(*),1) wr
FROM paper_positions WHERE trade_nature='scalp' AND status='closed'
  AND closed_at >= now()-interval '14 days'
GROUP BY 1 ORDER BY 1"""):
    print("   ", r)

print("[H] 手续费贡献：近14天 scalp partial_fee_paid 合计 vs pnl")
for r in q("""
SELECT count(*), round(sum(partial_fee_paid)::numeric,2),
  round(sum(partial_realized_pnl+unrealized_pnl)::numeric,2)
FROM paper_positions WHERE trade_nature='scalp' AND status='closed'
  AND closed_at >= now()-interval '14 days'"""):
    print("   n / fees / pnl:", r)

conn.rollback(); conn.close()
