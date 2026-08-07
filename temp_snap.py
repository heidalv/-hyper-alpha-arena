import os
os.environ['NO_PROXY']='127.0.0.1,localhost'; os.environ['no_proxy']='127.0.0.1,localhost'
os.environ.pop('HTTP_PROXY',None); os.environ.pop('http_proxy',None)
os.environ.pop('HTTPS_PROXY',None); os.environ.pop('https_proxy',None)
import sys, time, threading, urllib.request, json
sys.path.insert(0, r'D:\001Alpha\Hyper-Alpha-Arena')
from dotenv import load_dotenv
load_dotenv(r'D:\001Alpha\Hyper-Alpha-Arena\.env', override=True)
from sqlalchemy import create_engine, text
eng = create_engine('postgresql+psycopg://db_admin:YOUR_DB_PASSWORD@localhost:5432/alpha_arena')
stop = threading.Event()
def snap():
    for _ in range(60):
        try:
            with eng.connect() as c:
                rows = c.execute(text("SELECT pid, application_name, datname, state, left(query,250) FROM pg_stat_activity WHERE datname='alpha_arena' AND pid<>pg_backend_pid() AND query NOT ILIKE '%pg_stat_activity%'")).fetchall()
                for r in rows:
                    print('SNAP', r[0], r[1], r[2], r[3], (r[4] or '')[:120])
        except Exception as e:
            print('SNAP ERR', e)
        time.sleep(0.25)
threading.Thread(target=snap, daemon=True).start()
req = urllib.request.Request("http://127.0.0.1:8000/api/auth/login", data=json.dumps({"username":"heidalv@outlook.com","password":"CCf184215~"}).encode(), headers={"Content-Type":"application/json"})
tok = json.loads(urllib.request.urlopen(req, timeout=20).read())["access_token"]
req2 = urllib.request.Request("http://127.0.0.1:8000/api/full-auto/sessions", headers={"Authorization":"Bearer "+tok})
resp = json.loads(urllib.request.urlopen(req2, timeout=20).read())
print('API SESSIONS JSON:', json.dumps(resp, ensure_ascii=False)[:800])
time.sleep(3)
stop.set()
