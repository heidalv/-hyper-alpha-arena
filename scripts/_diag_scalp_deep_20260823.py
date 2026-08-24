# -*- coding: utf-8 -*-
"""短线深挖 v1: 日志拦截分布 + DB 盈亏结构 + 策略禁用排查 (2026-08-23)"""
import io, re, glob, os, json
from collections import Counter, defaultdict

REPORT = []
def w(s=''):
    REPORT.append(s)

# ---------- Part 1: 日志扫描 ----------
LOGDIR = r'D:\001Alpha\Hyper-Alpha-Arena\logs'
TODAY = '2026-08-23'

logfiles = [r'D:\001Alpha\Hyper-Alpha-Arena\logs\backend.log']
logfiles += sorted(glob.glob(os.path.join(LOGDIR, 'backend.pid*.log')))
# 只保留今天修改过的
logfiles = [f for f in logfiles if os.path.exists(f) and TODAY in __import__('datetime').datetime.fromtimestamp(os.path.getmtime(f)).strftime('%Y-%m-%d')]
w(f'[日志文件] {len(logfiles)} 个今日文件: ' + ', '.join(os.path.basename(f) for f in logfiles[:12]))

cat = Counter()          # 类别计数
gate_reason = Counter()  # gate reason 计数
hour_gate = Counter()    # 每小时 gate 拦截
hour_signal = Counter()  # 每小时信号(sell/buy)
hour_open = Counter()    # 每小时开仓
samples = defaultdict(list)

re_gate = re.compile(r'Gate拦截.*?reason=([^\s]*?)(?:——| id=|$)')
re_ts = re.compile(r'^(\d{4}-\d{2}-\d{2}) (\d{2}):')

def bucket_gate_reason(rs):
    # reason 形如 空头条件未齐(mid_bias=...,...) —— 取括号前主键
    key = rs.split('(')[0].strip()
    if not key:
        key = rs[:30]
    return key

for fp in logfiles:
    try:
        with io.open(fp, 'r', encoding='utf-8', errors='replace') as fh:
            for line in fh:
                mts = re_ts.match(line)
                if not mts or mts.group(1) != TODAY:
                    continue
                hh = int(mts.group(2))
                if '永久禁用' in line:
                    cat['lane_ban'] += 1
                    samples['lane_ban'].append(line.strip()[:160])
                elif '探索期' in line and ('软放行' in line or 'waiver' in line):
                    cat['ev_waiver'] += 1
                    samples['ev_waiver'].append(line.strip()[:160])
                elif 'Gate拦截' in line:
                    cat['gate'] += 1
                    hour_gate[hh] += 1
                    m = re_gate.search(line)
                    if m:
                        gate_reason[bucket_gate_reason(m.group(1))] += 1
                    else:
                        gate_reason['(未解析)'] += 1
                        samples['gate_unparsed'].append(line.strip()[:160])
                elif '→拦截' in line:
                    cat['micro_block'] += 1
                    hour_gate[hh] += 1
                    samples['micro'].append(line.strip()[:120])
                elif '近3笔胜率' in line or '近N笔胜率' in line:
                    cat['adaptive_wr'] += 1
                    samples['adaptive'].append(line.strip()[:140])
                elif 'ScalpCalib' in line:
                    cat['calib'] += 1
                    samples['calib'].append(line.strip()[:160])
                if 'ScalpRouter独立' in line and 'action=' in line:
                    m = re.search(r'action=(\w+)', line)
                    if m and m.group(1) in ('buy', 'sell'):
                        hour_signal[hh] += 1
                if '开仓' in line and ('成功' in line or 'OPEN' in line or '下单' in line):
                    hour_open[hh] += 1
                    cat['open'] += 1
                    samples['open'].append(line.strip()[:160])
    except Exception as e:
        w(f'  !! 读 {os.path.basename(fp)} 失败: {e}')

w('\n[1] 今日日志类别计数:')
for k, v in cat.most_common(20):
    w(f'   {k:16s} {v}')
w('\n[2] Gate拦截 reason TOP20:')
for k, v in gate_reason.most_common(20):
    w(f'   {v:5d}  {k}')
w('\n[3] 每小时: 信号数 / gate+micro拦截 / 开仓')
for h in range(24):
    w(f'   {h:02d}h  sig={hour_signal.get(h,0):4d}  block={hour_gate.get(h,0):4d}  open={hour_open.get(h,0):3d}')
w('\n[4] 样例行:')
for k in ('lane_ban', 'ev_waiver', 'gate_unparsed', 'micro', 'adaptive', 'open'):
    if samples.get(k):
        w(f'   -- {k} ({len(samples[k])}条, 显示前3):')
        for s in samples[k][:3]:
            w(f'      {s}')

# ---------- Part 2: DB ----------
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

w('\n[5] paper_positions 列名:')
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='paper_positions' ORDER BY ordinal_position")
cols = [r[0] for r in cur.fetchall()]
w('   ' + ', '.join(cols))
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='scalp_signal_log' ORDER BY ordinal_position")
_sig_cols = [r[0] for r in cur.fetchall()]
w('   scalp_signal_log: ' + ', '.join(_sig_cols))

def has(*names):
    return next((n for n in names if n in cols), None)

side_col = has('side', 'direction')
score_col = has('entry_score', 'signal_score', 'score')
strategy_col = has('strategy_id', 'strategy')

w(f'\n[6] 今日 scalp 开仓: {side_col=} {score_col=} {strategy_col=}')
if side_col and score_col:
    w('   按小时 x 方向:')
    for r in q(f"""
        SELECT EXTRACT(HOUR FROM opened_at)::int h, COALESCE({side_col},'?'), count(*),
          round(avg(COALESCE({score_col},0))::numeric,1), round(sum(COALESCE(partial_realized_pnl,0)+COALESCE(unrealized_pnl,0))::numeric,3)
        FROM paper_positions WHERE trade_nature='scalp' AND opened_at >= date_trunc('day', now())
        GROUP BY 1,2 ORDER BY 1,2"""):
        w(f'     {r[0]:02d}h {str(r[1]):6s} n={r[2]:3d} avg_score={str(r[3]):>5s} pnl={r[4]}')

w('\n[7] 今日 scalp close_reason 结构:')
for r in q("""
    SELECT COALESCE(close_reason,'open'), count(*),
      round(sum(COALESCE(partial_realized_pnl,0)+COALESCE(unrealized_pnl,0))::numeric,3) pnl,
      round(avg(COALESCE(partial_fee_paid,0))::numeric,3) avg_fee,
      round(avg(EXTRACT(EPOCH FROM (closed_at-opened_at))/60.0)::numeric,1) avg_min
    FROM paper_positions WHERE trade_nature='scalp' AND opened_at >= date_trunc('day', now())
    GROUP BY 1 ORDER BY 2 DESC"""):
    w(f'     {str(r[0]):18s} n={r[1]:3d} pnl={str(r[2]):>8s} avg_fee={str(r[3]):>6s} avg_min={str(r[4]):>6s}')

if side_col:
    w('\n[8] 今日 scalp 按方向:')
    for r in q(f"""
        SELECT {side_col}, count(*),
          round(sum(COALESCE(partial_realized_pnl,0)+COALESCE(unrealized_pnl,0))::numeric,3) pnl,
          round(100.0*sum(CASE WHEN COALESCE(partial_realized_pnl,0)+COALESCE(unrealized_pnl,0)>0 THEN 1 ELSE 0 END)/count(*),1) wr
        FROM paper_positions WHERE trade_nature='scalp' AND status='closed' AND opened_at >= date_trunc('day', now())
        GROUP BY 1"""):
        w(f'     {r[0]:6s} n={r[1]} pnl={r[2]} wr={r[3]}%')

if score_col:
    w('\n[9] 今日 scalp 平仓: entry_score 分桶 vs 盈亏:')
    for r in q(f"""
        SELECT width_bucket(COALESCE({score_col},0), 0, 100, 10) b, count(*),
          round(avg(COALESCE(partial_realized_pnl,0)+COALESCE(unrealized_pnl,0))::numeric,3),
          round(100.0*sum(CASE WHEN COALESCE(partial_realized_pnl,0)+COALESCE(unrealized_pnl,0)>0 THEN 1 ELSE 0 END)/count(*),1) wr
        FROM paper_positions WHERE trade_nature='scalp' AND status='closed' AND opened_at >= date_trunc('day', now())
        GROUP BY 1 ORDER BY 1"""):
        w(f'     分桶{r[0]:3d} n={r[1]:3d} avg_pnl={str(r[2]):>8s} wr={r[3]}%')

w('\n[10] 今日 scalp 按 symbol TOP15 (盈亏):')
for r in q("""
    SELECT symbol, count(*),
      round(sum(COALESCE(partial_realized_pnl,0)+COALESCE(unrealized_pnl,0))::numeric,3) pnl,
      round(100.0*sum(CASE WHEN COALESCE(partial_realized_pnl,0)+COALESCE(unrealized_pnl,0)>0 THEN 1 ELSE 0 END)/count(*),1) wr
    FROM paper_positions WHERE trade_nature='scalp' AND status='closed' AND opened_at >= date_trunc('day', now())
    GROUP BY 1 ORDER BY 3 ASC LIMIT 15"""):
    w(f'     {str(r[0]):12s} n={r[1]:3d} pnl={str(r[2]):>8s} wr={r[3]}%')

w('\n[11] symbol_removed 平仓明细 (今日):')
for r in q("""
    SELECT symbol, COALESCE(side,'?'), count(*),
      round(avg(EXTRACT(EPOCH FROM (closed_at-opened_at))/60.0)::numeric,1) avg_min,
      round(sum(COALESCE(partial_realized_pnl,0)+COALESCE(unrealized_pnl,0))::numeric,3) pnl
    FROM paper_positions WHERE trade_nature='scalp' AND close_reason='symbol_removed'
      AND opened_at >= date_trunc('day', now())
    GROUP BY 1,2 ORDER BY 3 DESC"""):
    w(f'     {r[0]:12s} {r[1]:6s} n={r[2]} avg_min={r[3]} pnl={r[4]}')

w('\n[12] 近7天 scalp_signal_log settled 全景:')
for r in q("""
    SELECT count(*),
      round(100.0*sum(CASE WHEN win THEN 1 ELSE 0 END)/count(*),1) wr,
      round(sum(net_ret)::numeric,3), round(avg(net_ret*100)::numeric,3) avg_ret_pct
    FROM scalp_signal_log WHERE settled=true
      AND signal_ts >= extract(epoch from now() - interval '7 days')"""):
    w(f'     n={r[0]} wr={r[1]}% sum_net_ret={r[2]} avg_net_ret%={r[3]}')
w('   按 horizon_sec:')
for r in q("""
    SELECT COALESCE(horizon_sec,0), count(*),
      round(100.0*sum(CASE WHEN win THEN 1 ELSE 0 END)/count(*),1) wr,
      round(sum(net_ret)::numeric,3)
    FROM scalp_signal_log WHERE settled=true
      AND signal_ts >= extract(epoch from now() - interval '7 days')
    GROUP BY 1 ORDER BY 2 DESC LIMIT 10"""):
    w(f'     horizon={r[0]:5d} n={r[1]:4d} wr={r[2]}% sum_ret={r[3]}')
w('   按 strategy:')
_sig_strat = next((c for c in ('strategy_id', 'strategy', 'lane', 'template_id') if c in _sig_cols), None)
if _sig_strat:
    for r in q(f"""
        SELECT COALESCE({_sig_strat},'?'), count(*),
          round(100.0*sum(CASE WHEN win THEN 1 ELSE 0 END)/count(*),1) wr,
          round(sum(net_ret)::numeric,3)
        FROM scalp_signal_log WHERE settled=true
          AND signal_ts >= extract(epoch from now() - interval '7 days')
        GROUP BY 1 ORDER BY 2 DESC LIMIT 12"""):
        w(f'     {str(r[0])[:28]:28s} n={r[1]:4d} wr={r[2]}% sum_ret={r[3]}')
else:
    w(f'     (无 strategy 类列; 列={_sig_cols})')

w('\n[13] AIStrategy 永久禁用/失效:')
try:
    for r in q("""
        SELECT strategy_id, is_active, COALESCE(genome::text,'') FROM ai_strategies
        WHERE is_active='false' OR genome::text LIKE '%permanently_disabled%' OR genome::text LIKE '%disable_reason%'
        ORDER BY strategy_id LIMIT 30"""):
        g = r[2][:120]
        w(f'     {r[0][:36]:36s} active={r[1]} genome={g}')
except Exception as e:
    w(f'     !! {e}')

w('\n[14] 今日 paper_positions 全部 trade_nature 分布:')
for r in q("""
    SELECT trade_nature, count(*),
      round(sum(COALESCE(partial_realized_pnl,0)+COALESCE(unrealized_pnl,0))::numeric,3) pnl
    FROM paper_positions WHERE opened_at >= date_trunc('day', now())
    GROUP BY 1 ORDER BY 2 DESC"""):
    w(f'     {r[0]:12s} n={r[1]} pnl={r[2]}')

conn.rollback(); conn.close()

out = os.path.join(r'D:\001Alpha\Hyper-Alpha-Arena\reports', '_短线深挖_20260823.txt')
with io.open(out, 'w', encoding='utf-8') as fh:
    fh.write('\n'.join(REPORT))
print(f'DONE -> {out} ({len(REPORT)} lines)')
