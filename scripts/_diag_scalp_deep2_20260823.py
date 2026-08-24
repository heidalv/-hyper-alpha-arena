# -*- coding: utf-8 -*-
"""短线深挖 v2: 信号质量(score×方向)、开仓匹配、校准时间线、持仓时限、env配置"""
import io, re, os
from collections import Counter, defaultdict
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
    cur.execute(sql, args)
    return cur.fetchall()

# ---- [1] 7天 settled 信号: factor_score 分桶 × 方向 ----
w('[1] scalp_signal_log 7d settled: factor_score 分桶 × 方向 (wr / sum_net_ret)')
for r in q("""
    SELECT width_bucket(COALESCE(factor_score,0), 0, 100, 10) b, direction, count(*),
      round(100.0*sum(CASE WHEN win THEN 1 ELSE 0 END)/count(*),1) wr,
      round(sum(net_ret)::numeric,3) net
    FROM scalp_signal_log WHERE settled=true
      AND signal_ts >= extract(epoch from now() - interval '7 days')
    GROUP BY 1,2 ORDER BY 1,2"""):
    w(f'    桶{r[0]:2d}({(r[0]-1)*10}-{r[0]*10}) {str(r[1]):7s} n={r[2]:5d} wr={r[3]}% net={r[4]}')

w('\n[2] 同: threshold 分桶 (阈值分布)')
for r in q("""
    SELECT COALESCE(threshold,0), count(*),
      round(100.0*sum(CASE WHEN win THEN 1 ELSE 0 END)/count(*),1) wr
    FROM scalp_signal_log WHERE settled=true
      AND signal_ts >= extract(epoch from now() - interval '7 days')
    GROUP BY 1 ORDER BY 2 DESC LIMIT 12"""):
    w(f'    threshold={str(r[0]):>5s} n={r[1]:5d} wr={r[2]}%')

w('\n[3] 今日信号 factor_score 分布 (全部, 未settled也算) × 方向')
for r in q("""
    SELECT width_bucket(COALESCE(factor_score,0), 0, 100, 10) b, direction, count(*)
    FROM scalp_signal_log
    WHERE signal_ts >= extract(epoch from date_trunc('day', now()))
    GROUP BY 1,2 ORDER BY 1,2"""):
    w(f'    桶{r[0]:2d}({(r[0]-1)*10}-{r[0]*10}) {str(r[1]):7s} n={r[2]:5d}')

w('\n[3b] 今日信号: 按小时 × 方向')
for r in q("""
    SELECT EXTRACT(HOUR FROM to_timestamp(signal_ts))::int h, direction, count(*)
    FROM scalp_signal_log
    WHERE signal_ts >= extract(epoch from date_trunc('day', now()))
    GROUP BY 1,2 ORDER BY 1,2"""):
    w(f'    {r[0]:02d}h {str(r[1]):7s} n={r[2]}')

w('\n[4] 今日开仓匹配信号 (symbol+side+entry_price 匹配, 1min 内):')
for r in q("""
    SELECT p.symbol, p.side, s.factor_score, s.threshold, p.close_reason,
      round((COALESCE(p.partial_realized_pnl,0)+COALESCE(p.unrealized_pnl,0))::numeric,3) pnl
    FROM paper_positions p
    LEFT JOIN LATERAL (
      SELECT s.factor_score, s.threshold FROM scalp_signal_log s
      WHERE s.symbol=p.symbol AND s.direction=p.side
        AND ABS(EXTRACT(EPOCH FROM (p.opened_at - to_timestamp(s.signal_ts)))) < 90
        AND s.signal_ts >= extract(epoch from p.opened_at - interval '2 minutes')
      ORDER BY ABS(EXTRACT(EPOCH FROM (p.opened_at - to_timestamp(s.signal_ts)))) ASC LIMIT 1
    ) s ON true
    WHERE p.trade_nature='scalp' AND p.opened_at >= date_trunc('day', now())
    ORDER BY p.opened_at"""):
    w(f'    {str(r[0]):8s} {str(r[1]):5s} score={str(r[2]):>4s} thr={str(r[3]):>4s} close={str(r[4]):16s} pnl={r[5]}')

w('\n[5] 今日开仓 expected_hold_hours / margin / leverage:')
for r in q("""
    SELECT count(*),
      round(avg(COALESCE(expected_hold_hours,0))::numeric,2) avg_exp_hrs,
      round(avg(COALESCE(margin,0))::numeric,2) avg_margin,
      round(avg(COALESCE(leverage,0))::numeric,1) avg_lev,
      round(sum(COALESCE(margin,0))::numeric,2) sum_margin
    FROM paper_positions WHERE trade_nature='scalp' AND opened_at >= date_trunc('day', now())"""):
    w(f'    n={r[0]} avg_exp_hrs={r[1]} avg_margin={r[2]} avg_lev={r[3]} sum_margin={r[4]}')

w('\n[6] 今日 scalp 平仓: 实际持仓分钟 vs pnl (前20):')
for r in q("""
    SELECT symbol, side, round(EXTRACT(EPOCH FROM (closed_at-opened_at))/60.0::numeric,1) mins,
      close_reason, round((COALESCE(partial_realized_pnl,0)+COALESCE(unrealized_pnl,0))::numeric,3) pnl
    FROM paper_positions WHERE trade_nature='scalp' AND status='closed' AND opened_at >= date_trunc('day', now())
    ORDER BY pnl ASC LIMIT 20"""):
    w(f'    {str(r[0]):8s} {str(r[1]):5s} {r[2]:>5}min {str(r[3]):16s} pnl={r[4]}')

w('\n[7] 近7天 scalp 平仓按 close_reason (盈亏/胜率):')
for r in q("""
    SELECT COALESCE(close_reason,'?'), count(*),
      round(sum(COALESCE(partial_realized_pnl,0)+COALESCE(unrealized_pnl,0))::numeric,2) pnl,
      round(100.0*sum(CASE WHEN COALESCE(partial_realized_pnl,0)+COALESCE(unrealized_pnl,0)>0 THEN 1 ELSE 0 END)/count(*),1) wr,
      round(avg(EXTRACT(EPOCH FROM (closed_at-opened_at))/60.0)::numeric,1) avg_min
    FROM paper_positions WHERE trade_nature='scalp' AND status='closed'
      AND opened_at >= now()-interval '7 days'
    GROUP BY 1 ORDER BY 2 DESC"""):
    w(f'    {str(r[0]):18s} n={r[1]:4d} pnl={str(r[2]):>8s} wr={r[3]}% avg_min={r[4]}')

w('\n[8] 近7天 scalp 平仓: 持仓分钟分桶 vs pnl')
for r in q("""
    SELECT width_bucket(EXTRACT(EPOCH FROM (closed_at-opened_at))/60.0, 0, 180, 9) b, count(*),
      round(sum(COALESCE(partial_realized_pnl,0)+COALESCE(unrealized_pnl,0))::numeric,2),
      round(100.0*sum(CASE WHEN COALESCE(partial_realized_pnl,0)+COALESCE(unrealized_pnl,0)>0 THEN 1 ELSE 0 END)/count(*),1) wr
    FROM paper_positions WHERE trade_nature='scalp' AND status='closed'
      AND opened_at >= now()-interval '7 days'
    GROUP BY 1 ORDER BY 1"""):
    w(f'    {(r[0]-1)*20:>3}-{r[0]*20:>3}min n={r[1]:4d} pnl={str(r[2]):>8s} wr={r[3]}%')

conn.rollback(); conn.close()

# ---- [9] 校准时间线: 每小时 calib/no-edge 计数 + waiver 计数 ----
w('\n[9] 校准 no-edge 每小时计数 (今日日志):')
LOGF = r'D:\001Alpha\Hyper-Alpha-Arena\logs\backend.log'
cnt = Counter()
evcnt = Counter()
noedge_h = Counter()
with io.open(LOGF, 'r', encoding='utf-8', errors='replace') as fh:
    for line in fh:
        m = re.match(r'^(\d{4}-\d{2}-\d{2}) (\d{2}):', line)
        if not m or m.group(1) != '2026-08-23':
            continue
        h = int(m.group(2))
        if 'ScalpCalib' in line:
            cnt[h] += 1
            if 'threshold=None' in line or '无盈利' in line:
                noedge_h[h] += 1
        if '探索期软放行' in line:
            evcnt[h] += 1
for h in range(24):
    if cnt.get(h) or evcnt.get(h):
        w(f'    {h:02d}h calib={cnt.get(h,0):5d} noedge={noedge_h.get(h,0):5d} ev_waiver={evcnt.get(h,0)}')

# ---- [10] env 配置 ----
w('\n[10] .env SCALP/短线相关配置:')
envp = r'D:\001Alpha\Hyper-Alpha-Arena\.env'
try:
    with io.open(envp, 'r', encoding='utf-8', errors='replace') as fh:
        for line in fh:
            ls = line.strip()
            if ls.startswith('#'):
                continue
            if re.match(r'^[A-Z0-9_]+=', ls):
                k = ls.split('=')[0]
                if any(t in k for t in ('SCALP', 'V5_', 'PAPER_SCALP', 'SCALP_', 'MAX_HOLD', 'HOLD')):
                    w(f'    {ls[:150]}')
except Exception as e:
    w(f'    !! {e}')

out = os.path.join(r'D:\001Alpha\Hyper-Alpha-Arena\reports', '_短线深挖2_20260823.txt')
with io.open(out, 'w', encoding='utf-8') as fh:
    fh.write('\n'.join(REPORT))
print(f'DONE -> {out} ({len(REPORT)} lines)')
