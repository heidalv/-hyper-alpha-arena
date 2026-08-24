# -*- coding: utf-8 -*-
"""短线深挖 v3: 显式日期 2026-08-23 重跑 + strategy/结算/滑点结构"""
import io, re, os
from collections import Counter
import psycopg

REPORT = []
def w(s=''):
    REPORT.append(s)

conn = psycopg.connect('postgresql://laobao:alpha_pass@localhost:5432/alpha_arena')
conn.autocommit = False
cur = conn.cursor()
cur.execute("SET LOCAL app.tenant_id = '326'")
try:
    cur.execute("SET LOCAL app.is_admin = 'on'")
except Exception:
    pass

def q(sql, args=()):
    s = _sql(sql)
    try:
        cur.execute(s, args)
    except Exception as e:
        w(f'   !! SQL失败: {e} :: {s[:160]}')
        raise
    return cur.fetchall()

D = '2026-08-23'
DT0 = f"date '{D}'"
DT1 = f"(date '{D}' + interval '1 day')"

def _sql(s):
    return s.replace('__DT0__', DT0).replace('__DT1__', DT1)

w('[1] 8-23 信号: 按小时 × 方向')
for r in q("""
    SELECT EXTRACT(HOUR FROM to_timestamp(signal_ts))::int h, direction, count(*)
    FROM scalp_signal_log
    WHERE signal_ts >= extract(epoch from __DT0__) AND signal_ts < extract(epoch from __DT1__)
    GROUP BY 1,2 ORDER BY 1,2"""):
    w(f'    {r[0]:02d}h {str(r[1]):7s} n={r[2]}')

w('\n[2] 8-23 开仓 × strategy_id 前缀 (mr vs lane) 盈亏:')
for r in q("""
    SELECT CASE WHEN strategy_id LIKE 'scalp_mr%%' THEN 'MR' ELSE 'lane/other' END kind, side,
      count(*),
      round(sum(COALESCE(partial_realized_pnl,0)+COALESCE(unrealized_pnl,0))::numeric,3) pnl,
      round(100.0*sum(CASE WHEN COALESCE(partial_realized_pnl,0)+COALESCE(unrealized_pnl,0)>0 THEN 1 ELSE 0 END)/count(*),1) wr
    FROM paper_positions WHERE trade_nature='scalp'
      AND opened_at >= __DT0__ AND opened_at < __DT1__
    GROUP BY 1,2 ORDER BY 1,2"""):
    w(f'    {r[0]:10s} {str(r[1]):5s} n={r[2]:3d} pnl={r[3]} wr={r[4]}%')

w('\n[3] 8-23 开仓明细 (时间/symbol/side/strategy/margin/close/pnl):')
for r in q("""
    SELECT to_char(opened_at,'HH24:MI') t, symbol, side,
      CASE WHEN strategy_id LIKE 'scalp_mr%%' THEN 'MR' ELSE 'ln' END,
      round(COALESCE(margin,0)::numeric,2) mg,
      COALESCE(close_reason,'open'),
      round((COALESCE(partial_realized_pnl,0)+COALESCE(unrealized_pnl,0))::numeric,3) pnl
    FROM paper_positions WHERE trade_nature='scalp'
      AND opened_at >= __DT0__ AND opened_at < __DT1__
    ORDER BY opened_at"""):
    w(f'    {r[0]} {str(r[1]):8s} {str(r[2]):5s} {r[3]} mg={r[4]:>6} {str(r[5]):16s} pnl={r[6]}')

w('\n[4] 8-23 SL 命中明细:')
for r in q("""
    SELECT symbol, side, to_char(opened_at,'HH24:MI'),
      round((100.0*ABS(entry_price-sl_price)/entry_price)::numeric,2) sl_pct,
      round((100.0*ABS(tp_price-entry_price)/entry_price)::numeric,2) tp_pct,
      round((EXTRACT(EPOCH FROM (closed_at-opened_at))/60.0)::numeric,1) mins,
      round(COALESCE(margin,0)::numeric,2) mg,
      round((COALESCE(partial_realized_pnl,0)+COALESCE(unrealized_pnl,0))::numeric,3) pnl
    FROM paper_positions WHERE trade_nature='scalp' AND close_reason='sl'
      AND opened_at >= __DT0__ AND opened_at < __DT1__
    ORDER BY opened_at"""):
    w(f'    {str(r[0]):8s} {str(r[1]):5s} {r[2]} sl={r[3]}% tp={r[4]}% {r[5]:>5}min mg={r[6]:>6} pnl={r[7]}')

w('\n[5] 近7天 scalp 平仓: SL距离分桶 vs 命中率:')
for r in q("""
    SELECT width_bucket(100.0*ABS(entry_price-sl_price)/entry_price, 0.4, 1.8, 7) b, count(*) all_n,
      count(*) FILTER (WHERE close_reason='sl') sl_n,
      round(sum(COALESCE(partial_realized_pnl,0)+COALESCE(unrealized_pnl,0))::numeric,2) pnl
    FROM paper_positions WHERE trade_nature='scalp' AND status='closed'
      AND opened_at >= now()-interval '7 days'
    GROUP BY 1 ORDER BY 1"""):
    w(f'    sl%桶{r[0]} n={r[1]:4d} sl_hit={r[2]:3d} ({round(100.0*r[2]/max(r[1],1),1)}%) pnl={r[3]}')

w('\n[6] 近7天 scalp 平仓: TP距离分桶 vs 命中率:')
for r in q("""
    SELECT width_bucket(100.0*ABS(tp_price-entry_price)/entry_price, 0.4, 2.2, 9) b, count(*) all_n,
      count(*) FILTER (WHERE close_reason='tp') tp_n,
      count(*) FILTER (WHERE close_reason='breakeven_tp') be_n,
      round(sum(COALESCE(partial_realized_pnl,0)+COALESCE(unrealized_pnl,0))::numeric,2) pnl
    FROM paper_positions WHERE trade_nature='scalp' AND status='closed'
      AND opened_at >= now()-interval '7 days'
    GROUP BY 1 ORDER BY 1"""):
    w(f'    tp%桶{r[0]} n={r[1]:4d} tp_hit={r[2]:3d} be_hit={r[3]:3d} pnl={r[4]}')

w('\n[7] 近7天 settled 信号 settle_note 分布:')
for r in q("""
    SELECT COALESCE(settle_note,'?'), count(*),
      round(100.0*sum(CASE WHEN win THEN 1 ELSE 0 END)/count(*),1) wr,
      round(sum(net_ret)::numeric,3)
    FROM scalp_signal_log WHERE settled=true
      AND signal_ts >= extract(epoch from now() - interval '7 days')
    GROUP BY 1 ORDER BY 2 DESC LIMIT 10"""):
    w(f'    {str(r[0])[:40]:40s} n={r[1]:5d} wr={r[2]}% net={r[3]}')

w('\n[8] 近7天 settled: net_ret 分布 (是否对称/成本吞噬):')
for r in q("""
    SELECT COALESCE(width_bucket(net_ret*100, -2, 2, 20),0) b, count(*),
      round(avg(net_ret*100)::numeric,3)
    FROM scalp_signal_log WHERE settled=true
      AND signal_ts >= extract(epoch from now() - interval '7 days')
    GROUP BY 1 ORDER BY 1"""):
    w(f'    {(r[0]-1)*0.2-2:>5.1f}~{r[0]*0.2-2:>5.1f}% n={r[1]:5d} avg={r[2]}')

w('\n[9] 8-23 XPL 全部信号日志 (settled):')
for r in q("""
    SELECT to_char(to_timestamp(signal_ts),'HH24:MI') t, direction, factor_score, COALESCE(threshold,0),
      win, round((COALESCE(net_ret,0)*100)::numeric,3), COALESCE(settle_note,'')
    FROM scalp_signal_log WHERE symbol='XPL' AND settled=true
      AND signal_ts >= extract(epoch from __DT0__) AND signal_ts < extract(epoch from __DT1__)
    ORDER BY signal_ts"""):
    w(f'    {r[0]} {str(r[1]):5s} score={str(r[2]):>3s} thr={str(r[3]):>4s} win={r[4]} ret%={r[5]} {str(r[6])[:36]}')

conn.rollback(); conn.close()

w('\n[10] 8-23 日志: 自适应门槛提高 (近N笔胜率<30%) 按 symbol 计数:')
LOGF = r'D:\001Alpha\Hyper-Alpha-Arena\logs\backend.log'
ada = Counter()
files = [LOGF]
with io.open(LOGF, 'r', encoding='utf-8', errors='replace') as fh:
    for line in fh:
        m = re.match(r'^(\d{4}-\d{2}-\d{2}) ', line)
        if not m or m.group(1) != D:
            continue
        m2 = re.search(r'\[ScalpRouter\] (\w+) 近\d+笔胜率(\d+)%<30%', line)
        if m2:
            ada[m2.group(1)] += 1
for k, v in ada.most_common(30):
    w(f'    {k:10s} {v}')

out = os.path.join(r'D:\001Alpha\Hyper-Alpha-Arena\reports', '_短线深挖3_20260823.txt')
with io.open(out, 'w', encoding='utf-8') as fh:
    fh.write('\n'.join(REPORT))
print(f'DONE -> {out} ({len(REPORT)} lines)')
