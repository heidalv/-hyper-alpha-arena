"""深度调查综合诊断：会话 / 决策快照 / MLTO thesis / K线新鲜度 / 数据中心心跳"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psycopg2

def load_env():
    d = {}
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    with open(p, encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            d[k.strip()] = v.strip().strip('"').strip("'")
    return d

env = load_env()
DSNS = {
    'arena': env.get('DATABASE_URL', 'postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena'),
    'analytics': env.get('ANALYTICS_DATABASE_URL', 'postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_analytics'),
    'market': env.get('MARKET_DATABASE_URL', 'postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market'),
    'snapshots': env.get('SNAPSHOTS_DATABASE_URL', 'postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_snapshots'),
}

def parse_dsn(dsn):
    body = dsn.split('://', 1)[1]
    cred, rest = body.split('@', 1)
    user, pw = cred.split(':', 1)
    hostport, dbname = rest.rsplit('/', 1)
    host, port = (hostport.split(':', 1) if ':' in hostport else (hostport, '5432'))
    return user, pw, host, port, dbname

def q(db, sql, label, limit=15):
    try:
        user, pw, host, port, dbn = parse_dsn(DSNS[db])
        conn = psycopg2.connect(host=host, port=port, user=user, password=pw, dbname=dbn)
        conn.set_client_encoding('UTF8')
        with conn.cursor() as cur:
            cur.execute("SET app.is_admin='on'")
            cur.execute(sql)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description] if cur.description else []
            print(f'\n=== [{dbn}] {label} ({len(rows)} rows) ===')
            for r in rows[:limit]:
                print('  ', dict(zip(cols, r)))
        conn.close()
    except Exception as ex:
        print(f'[{db}] {label} ERROR: {ex}')

# 1. 会话状态
q('arena', "SELECT id, session_id, status, symbols, account_id, updated_at FROM full_auto_sessions ORDER BY updated_at DESC LIMIT 8", '全自动会话')

# 2. MLTO thesis 最新
q('analytics', "SELECT thesis_id, session_id, symbol, tier, direction, llm_conviction, open_readiness, tranche_stage, consistency, updated_at FROM mlto_thesis ORDER BY updated_at DESC LIMIT 12", 'MLTO thesis 最新')

# 3. 决策快照 各类别计数（最近24h）
q('analytics', """
SELECT source_lane, tier, action, count(*) c, max(timestamp) last_ts
FROM decision_snapshots
WHERE timestamp > now() - interval '24 hours'
GROUP BY source_lane, tier, action ORDER BY c DESC LIMIT 20
""", '决策快照 24h 汇总')

# 4. 数据中心心跳
q('market', "SELECT exchange, period, pool, last_success_at, symbols_ok, symbols_fail FROM kline_sync_heartbeat ORDER BY updated_at DESC LIMIT 10", 'kline_sync_heartbeat')
q('market', "SELECT exchange, symbol, period, status, progress, total_records, collected_records, updated_at FROM kline_collection_tasks ORDER BY updated_at DESC LIMIT 5", 'kline_collection_tasks')

# 5. K线新鲜度（各交易所/周期最近更新时间）
q('market', """
SELECT exchange, period, max(timestamp) last_ts, now() - max(timestamp) AS age
FROM crypto_klines
WHERE timestamp > now() - interval '30 days'
GROUP BY exchange, period
ORDER BY max(timestamp) DESC LIMIT 25
""", 'K线新鲜度 按交易所周期')

