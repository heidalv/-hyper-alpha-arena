"""深度调查 v5：K线秒级epoch + AI池币数据覆盖检查"""
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

def q(sql, dsn, label, limit=20):
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

MARKET = env['MARKET_DATABASE_URL']
ANALYTICS = env['ANALYTICS_DATABASE_URL']

# 1. K线新鲜度（epoch秒）
q("""
SELECT exchange, period, max(timestamp) AS last_ts,
       (EXTRACT(EPOCH FROM now()) - max(timestamp)) / 60 AS age_minutes,
       count(*) AS rows
FROM crypto_klines
WHERE timestamp > EXTRACT(EPOCH FROM now()) - 30*86400
GROUP BY exchange, period
ORDER BY max(timestamp) DESC LIMIT 30
""", MARKET, 'K线新鲜度 交易所×周期 (epoch秒)')

# 2. AI池币在各交易所的数据覆盖（是否有4h/1d长线周期）
q("""
SELECT symbol, exchange, period, max(timestamp) AS last_ts,
       (EXTRACT(EPOCH FROM now()) - max(timestamp)) / 60 AS age_minutes,
       count(*) AS rows
FROM crypto_klines
WHERE symbol IN ('ENA','FARTCOIN','KAITO','KPEPE','TSLA','UNI','ZEC')
  AND period IN ('15m','1h','4h','1d')
GROUP BY symbol, exchange, period
ORDER BY symbol, period
""", MARKET, 'AI池币 长线周期数据覆盖')

# 3. AI池币 短线周期覆盖（master 用 1m/5m/15m）
q("""
SELECT symbol, exchange, period, max(timestamp) AS last_ts,
       (EXTRACT(EPOCH FROM now()) - max(timestamp)) / 60 AS age_minutes
FROM crypto_klines
WHERE symbol IN ('ENA','FARTCOIN','KAITO','KPEPE','TSLA','UNI','ZEC')
  AND period IN ('1m','5m','15m') AND exchange='asterdex'
GROUP BY symbol, exchange, period
ORDER BY symbol, period
""", MARKET, 'AI池币 asterdex 短线周期')

# 4. AI池决策实际 source_lane 记录（近7天）
q("""
SELECT source_lane, symbol, action, count(*) c, max(timestamp) last_ts
FROM decision_snapshots
WHERE symbol IN ('ENA','FARTCOIN','KAITO','KPEPE','TSLA','UNI','ZEC')
  AND timestamp > now() - interval '7 days'
GROUP BY source_lane, symbol, action
ORDER BY c DESC LIMIT 20
""", ANALYTICS, 'AI池币 决策记录')

# 5. master 对 AI 池币最近决策全貌
q("""
SELECT symbol, tier, action, direction, confidence, timestamp
FROM decision_snapshots
WHERE symbol IN ('ENA','FARTCOIN','KAITO','KPEPE','TSLA','UNI','ZEC')
  AND source_lane='master'
ORDER BY timestamp DESC LIMIT 15
""", ANALYTICS, 'master AI池币决策')
