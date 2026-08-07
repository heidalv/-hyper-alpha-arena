"""深度调查 v3：类型确认 + K线新鲜度 + master长线详情"""
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

def parse_dsn(dsn):
    body = dsn.split('://', 1)[1]
    cred, rest = body.split('@', 1)
    user, pw = cred.split(':', 1)
    hostport, dbname = rest.rsplit('/', 1)
    host, port = (hostport.split(':', 1) if ':' in hostport else (hostport, '5432'))
    return user, pw, host, port, dbname

def get_conn(dsn):
    u, p, h, po, db = parse_dsn(dsn)
    conn = psycopg2.connect(host=h, port=po, user=u, password=p, dbname=db)
    conn.set_client_encoding('UTF8')
    return conn

def q(sql, dsn, label, limit=15):
    try:
        conn = get_conn(dsn)
        with conn.cursor() as cur:
            cur.execute("SET app.is_admin='on'")
            cur.execute(sql)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description] if cur.description else []
            print(f'\n=== {label} ({len(rows)} rows) ===')
            for r in rows[:limit]:
                print('  ', dict(zip(cols, r)))
        conn.close()
    except Exception as ex:
        print(f'{label} ERROR: {ex}')

# 类型
conn = get_conn(env['MARKET_DATABASE_URL'])
with conn.cursor() as cur:
    cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='crypto_klines' AND column_name IN ('timestamp','created_at')")
    print('crypto_klines ts types:', cur.fetchall())
conn.close()
conn = get_conn(env['ANALYTICS_DATABASE_URL'])
with conn.cursor() as cur:
    cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='decision_snapshots' AND column_name IN ('timestamp','orchestrator_json','ai_reasoning')")
    print('decision_snapshots types:', cur.fetchall())
conn.close()

# K线新鲜度 按 交易所×周期
q("""
SELECT exchange, period, max(timestamp) AS last_ts,
       EXTRACT(EPOCH FROM (now() - max(timestamp)::timestamptz)) / 60 AS age_minutes,
       count(*) AS rows
FROM crypto_klines
WHERE timestamp > now() - interval '30 days'
GROUP BY exchange, period
ORDER BY max(timestamp) DESC LIMIT 30
""", env['MARKET_DATABASE_URL'], 'K线新鲜度 交易所×周期')

# BTC/ETH/SOL asterdex 长线周期
q("""
SELECT exchange, symbol, period, max(timestamp) AS last_ts,
       EXTRACT(EPOCH FROM (now() - max(timestamp)::timestamptz)) / 60 AS age_minutes
FROM crypto_klines
WHERE symbol IN ('BTC','ETH','SOL') AND exchange='asterdex' AND period IN ('15m','1h','4h','1d')
GROUP BY exchange, symbol, period
ORDER BY period, symbol
""", env['MARKET_DATABASE_URL'], 'BTC/ETH/SOL asterdex 长线周期')

# master 长线决策详情
q("""
SELECT symbol, tier, action, direction, confidence,
       left(ai_reasoning::text, 250) AS reasoning,
       timestamp
FROM decision_snapshots
WHERE source_lane = 'master' AND tier = 'long'
ORDER BY timestamp DESC LIMIT 8
""", env['ANALYTICS_DATABASE_URL'], 'master 长线决策详情')
