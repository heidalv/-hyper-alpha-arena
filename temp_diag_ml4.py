"""临时诊断脚本：检查 mlto_thesis / mlto_thesis_events / llm_usage_logs 最新状态"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psycopg2
from sqlalchemy import text

def load_env():
    d = {}
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'), encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                d[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return d

env = load_env()
def get(key, default):
    return os.getenv(key, env.get(key, default))

host = get('POSTGRES_HOST', 'localhost')
port = get('POSTGRES_PORT', '5432')
user = get('POSTGRES_USER', 'alpha_arena')
pw = get('POSTGRES_PASSWORD', 'alpha_arena_pg')
print('parsed:', repr(host), repr(port), repr(user), repr(pw)[:20])

for dbname in ['alpha_analytics', 'alpha_arena']:
    try:
        conn = psycopg2.connect(host=host, port=port, user=user, password=pw, dbname=dbname)
        conn.set_client_encoding('UTF8')
        with conn.cursor() as cur:
            cur.execute("SET app.is_admin='on'")
            if dbname == 'alpha_analytics':
                cur.execute('SELECT count(*) AS cnt, max(created_at) AS last_ts FROM llm_usage_logs')
                r = cur.fetchone()
                print(f'[{dbname}] llm_usage_logs: cnt={r[0]} last_ts={r[1]}')
                cur.execute("SELECT call_type, count(*) FROM llm_usage_logs WHERE created_at > now() - interval '1 hour' GROUP BY call_type ORDER BY 2 DESC LIMIT 10")
                r = cur.fetchall()
                print(f'[{dbname}] 近1小时 call_type 分布: {r}')
            else:
                cur.execute("SELECT count(*) AS cnt, max(updated_at) AS last_ts FROM mlto_thesis")
                r = cur.fetchone()
                print(f'[{dbname}] mlto_thesis: cnt={r[0]} last_ts={r[1]}')
                cur.execute("SELECT count(*) AS cnt, max(created_at) AS last_ts FROM mlto_thesis_events")
                r2 = cur.fetchone()
                print(f'[{dbname}] mlto_thesis_events: cnt={r2[0]} last_ts={r2[1]}')
                cur.execute("SELECT session_id, status FROM full_auto_sessions")
                r3 = cur.fetchall()
                print(f'[{dbname}] sessions: {r3}')
        conn.close()
    except Exception as ex:
        import traceback
        traceback.print_exc()
        print(f'[{dbname}] ERROR: {ex}')
