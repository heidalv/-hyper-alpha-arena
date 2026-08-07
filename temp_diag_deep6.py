"""深度调查 v6：master 最新决策 reasoning + 数据持续更新验证"""
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

ANALYTICS = env['ANALYTICS_DATABASE_URL']
MARKET = env['MARKET_DATABASE_URL']

# 1. master 最新 15 条决策（含 reasoning），确认是否仍报数据缺失
q("""
SELECT symbol, tier, action, confidence, left(ai_reasoning, 160) AS reasoning, timestamp
FROM decision_snapshots
WHERE source_lane='master'
ORDER BY timestamp DESC LIMIT 15
""", ANALYTICS, 'master 最新决策')

# 2. 现在 与 数据中心的延迟——BTC/ETH/SOL 各周期 asterdex 现在新鲜度
q("""
SELECT symbol, period, max(timestamp) AS last_ts,
       (EXTRACT(EPOCH FROM now()) - max(timestamp)) / 60 AS age_minutes
FROM crypto_klines
WHERE exchange='asterdex' AND symbol IN ('BTC','ETH','SOL') AND period IN ('1m','5m','15m','1h')
GROUP BY symbol, period ORDER BY period, symbol
""", MARKET, 'BTC/ETH/SOL asterdex 当前新鲜度')

# 3. 数据中心 heartbeat 中最新的几个
q("""
SELECT exchange, period, pool, last_success_at,
       EXTRACT(EPOCH FROM (now() - last_success_at)) / 60 AS age_minutes
FROM kline_sync_heartbeat ORDER BY last_success_at DESC LIMIT 6
""", MARKET, '数据中心心跳')
