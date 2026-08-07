"""深度调查 v8：KPEPE 数据断档 + TrendAgent独立路径 + 最新thesis叙事"""
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

# 1. KPEPE 各交易所覆盖（确认是否全所断档）
q("""
SELECT exchange, period, max(timestamp) AS last_ts,
       (EXTRACT(EPOCH FROM now()) - max(timestamp)) / 3600 AS age_hours,
       count(*) AS rows
FROM crypto_klines
WHERE symbol='KPEPE'
GROUP BY exchange, period ORDER BY exchange, period
""", MARKET, 'KPEPE 各所覆盖')

# 2. asterdex heartbeat 中 KPEPE 相关
q("""
SELECT * FROM kline_sync_heartbeat
WHERE exchange='asterdex' ORDER BY last_success_at DESC LIMIT 4
""", MARKET, 'asterdex 心跳')

# 3. 最新 thesis 叙事（确认 LLM 内容）
q("""
SELECT symbol, direction, llm_conviction, open_readiness,
       left(thesis_summary, 400) AS summary,
       updated_at
FROM mlto_thesis
WHERE session_id = 'fa_10d44c724e'
ORDER BY updated_at DESC LIMIT 3
""", ANALYTICS, '最新 thesis 叙事')

# 4. decision_snapshots 中 trend 独立路径最近决策
q("""
SELECT source_lane, symbol, tier, action, direction, confidence, timestamp
FROM decision_snapshots
WHERE (source_lane LIKE '%trend%' OR source_lane LIKE '%midlong%' OR source_lane LIKE '%mlto%')
  AND timestamp > now() - interval '48 hours'
ORDER BY timestamp DESC LIMIT 15
""", ANALYTICS, 'trend/midlong 决策')
