"""深度调查 v7：master 视角数据缺失核对 + MLTO open_gate + LLM流式content确认"""
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
ARENA = env['DATABASE_URL']

# 1. TSLA/KPEPE/ENA 在 asterdex 的全部周期覆盖（确认是否真缺失）
q("""
SELECT symbol, period, max(timestamp) AS last_ts,
       (EXTRACT(EPOCH FROM now()) - max(timestamp)) / 60 AS age_minutes,
       count(*) AS rows
FROM crypto_klines
WHERE exchange='asterdex' AND symbol IN ('TSLA','KPEPE','ENA','FARTCOIN','UNI','ZEC','KAITO')
GROUP BY symbol, period ORDER BY symbol, period
""", MARKET, 'AI池币 asterdex 全周期覆盖')

# 2. 指标/indicators 表是否存在且新鲜（master 说缺失 indicators）
q("""
SELECT column_name FROM information_schema.columns
WHERE table_name LIKE '%indicator%' LIMIT 15
""", MARKET, '指标表列')
q("""
SELECT table_name FROM information_schema.tables
WHERE table_schema='public' AND (table_name LIKE '%indicator%' OR table_name LIKE '%factor%')
""", MARKET, '指标相关表')

# 3. MLTO open_gate 相关: thesis 的 open_readiness 与 hub_composite
q("""
SELECT symbol, direction, llm_conviction, hub_composite, hub_adjusted, open_readiness,
       tranche_stage, consistency, stable_since, updated_at
FROM mlto_thesis
WHERE session_id = 'fa_10d44c724e'
ORDER BY updated_at DESC
""", ANALYTICS, 'MLTO thesis 全部字段')
