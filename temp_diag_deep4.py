"""深度调查 v4：K线新鲜度(epoch) + 数据缺失 + AI池相关表"""
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

MARKET = env['MARKET_DATABASE_URL']
ANALYTICS = env['ANALYTICS_DATABASE_URL']
ARENA = env['DATABASE_URL']

# 1. K线新鲜度（timestamp 为 epoch 毫秒）
q("""
SELECT exchange, period, max(timestamp) AS last_ts,
       (EXTRACT(EPOCH FROM now()) * 1000 - max(timestamp)) / 60000 AS age_minutes,
       count(*) AS rows
FROM crypto_klines
WHERE timestamp > (EXTRACT(EPOCH FROM now()) * 1000 - 30*86400*1000)
GROUP BY exchange, period
ORDER BY max(timestamp) DESC LIMIT 30
""", MARKET, 'K线新鲜度 交易所×周期 (epoch ms)')

# 2. BTC/ETH/SOL 各交易所 15m/1h 新鲜度
q("""
SELECT exchange, symbol, period, max(timestamp) AS last_ts,
       (EXTRACT(EPOCH FROM now()) * 1000 - max(timestamp)) / 60000 AS age_minutes
FROM crypto_klines
WHERE symbol IN ('BTC','ETH','SOL') AND period IN ('15m','1h')
GROUP BY exchange, symbol, period
ORDER BY exchange, period, symbol
""", MARKET, 'BTC/ETH/SOL 15m/1h 各交易所')

# 3. 会话的 auto_coin 配置
q("""
SELECT session_id, status, symbols, auto_coin_enabled, auto_coin_symbols, active_exchange, auto_coin_max_slots, updated_at
FROM full_auto_sessions
ORDER BY updated_at DESC LIMIT 3
""", ARENA, '会话 AI池配置')

# 4. AI池（auto_coin）相关: 决策快照里 auto 池决策
q("""
SELECT symbol, source_lane, action, direction, confidence, timestamp
FROM decision_snapshots
WHERE (source_lane LIKE '%auto%' OR source_lane LIKE '%coin%' OR source_lane LIKE '%ai%')
  AND timestamp > now() - interval '24 hours'
ORDER BY timestamp DESC LIMIT 12
""", ANALYTICS, 'AI池决策')

# 5. 缺失数据观察：SOL master_execute 数据门控中缺失的具体项
q("""
SELECT symbol, tier, action, left(ai_reasoning, 200) AS reasoning, timestamp
FROM decision_snapshots
WHERE source_lane='master' AND ai_reasoning LIKE '%缺失%'
  AND timestamp > now() - interval '24 hours'
ORDER BY timestamp DESC LIMIT 10
""", ANALYTICS, 'master 数据缺失门控')
