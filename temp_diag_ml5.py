"""临时诊断 v24：mlto_thesis_events 最新 conviction 变化"""
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
DSNS = {'analytics': env.get('ANALYTICS_DATABASE_URL', 'postgresql+psycopg://db_admin:YOUR_DB_PASSWORD@localhost:5432/alpha_analytics')}

def parse_dsn(dsn):
    body = dsn.split('://', 1)[1]
    cred, rest = body.split('@', 1)
    user, pw = cred.split(':', 1)
    hostport, dbname = rest.rsplit('/', 1)
    host, port = (hostport.split(':', 1) if ':' in hostport else (hostport, '5432'))
    return user, pw, host, port, dbname

def q(db, sql, label):
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
            for r in rows[:20]:
                print('  ', dict(zip(cols, r)))
        conn.close()
    except Exception as ex:
        print(f'[{db}] {label} ERROR: {ex}')

q('analytics', """
    SELECT column_name FROM information_schema.columns
    WHERE table_name='mlto_thesis_events' ORDER BY ordinal_position
""", 'mlto_thesis_events 列')

q('analytics', """
    SELECT * FROM mlto_thesis_events
    ORDER BY id DESC LIMIT 10
""", 'mlto_thesis_events 最新10')
