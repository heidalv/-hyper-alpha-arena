"""深度调查 v2：K线新鲜度 + LLM thesis 叙事 + master 长线决策原因"""
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

# 1. K线新鲜度：按 交易所×周期 最近更新时间与滞后
q('market', """
SELECT exchange, period,
       max(timestamp) AS last_ts,
       EXTRACT(EPOCH FROM (now() - max(timestamp))) / 60 AS age_minutes,
       count(*) AS rows_30d
FROM crypto_klines
WHERE timestamp > now() - interval '30 days'
GROUP BY exchange, period
ORDER BY max(timestamp) DESC LIMIT 30
""", 'K线新鲜度 交易所×周期')

# 2. 长线常用周期：BTC/ETH/SOL 在 asterdex 15m/1h 的最近数据
q('market', """
SELECT exchange, symbol, period, max(timestamp) AS last_ts,
       EXTRACT(EPOCH FROM (now() - max(timestamp))) / 60 AS age_minutes
FROM crypto_klines
WHERE symbol IN ('BTC','ETH','SOL') AND exchange='asterdex' AND period IN ('15m','1h','4h')
GROUP BY exchange, symbol, period
ORDER BY period, symbol
""", 'BTC/ETH/SOL asterdex 长线周期新鲜度')

# 3. MLTO thesis 叙事（看 LLM 为何低置信）
q('analytics', """
SELECT symbol, direction, llm_conviction, open_readiness,
       left(thesis_summary, 500) AS summary,
       updated_at
FROM mlto_thesis
WHERE session_id = 'fa_10d44c724e'
ORDER BY updated_at DESC LIMIT 4
""", 'MLTO thesis 叙事')

# 4. master lane long 决策最近详情（hold 原因）
q('analytics', """
SELECT symbol, tier, action, direction, confidence,
       left(ai_reasoning, 300) AS reasoning,
       left(orchestrator_json, 300) AS orch,
       timestamp
FROM decision_snapshots
WHERE source_lane = 'master' AND tier = 'long'
ORDER BY timestamp DESC LIMIT 8
""", 'master 长线决策详情')

# 5. trend 独立 lane 的决策（TrendAgent 独立路径）
q('analytics', """
SELECT source_lane, symbol, action, direction, confidence, timestamp
FROM decision_snapshots
WHERE source_lane LIKE '%trend%' AND timestamp > now() - interval '24 hours'
ORDER BY timestamp DESC LIMIT 10
""", 'trend lane 决策')
