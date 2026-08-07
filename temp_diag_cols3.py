"""查询真实表列名"""
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

def cols(url, table, dbn):
    user, pw, host, port, db = parse_dsn(url)
    conn = psycopg2.connect(host=host, port=port, user=user, password=pw, dbname=db)
    conn.set_client_encoding('UTF8')
    with conn.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s ORDER BY ordinal_position", (table,))
        print(dbn, table, ':', [r[0] for r in cur.fetchall()])
    conn.close()

cols(env.get('ANALYTICS_DATABASE_URL'), 'mlto_thesis', 'analytics')
cols(env.get('ANALYTICS_DATABASE_URL'), 'decision_snapshots', 'analytics')
cols(env.get('MARKET_DATABASE_URL'), 'crypto_klines', 'market')
cols(env.get('MARKET_DATABASE_URL'), 'kline_sync_heartbeat', 'market')
cols(env.get('ARENA_DATABASE_URL') or env.get('DATABASE_URL'), 'full_auto_sessions', 'arena')
