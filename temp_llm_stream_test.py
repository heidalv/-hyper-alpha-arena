import os, sys, json, time
sys.path.insert(0, r'D:\001Alpha\Hyper-Alpha-Arena')
os.environ['NO_PROXY']='127.0.0.1,localhost'; os.environ['no_proxy']='127.0.0.1,localhost'
os.environ.pop('HTTP_PROXY',None); os.environ.pop('http_proxy',None); os.environ.pop('HTTPS_PROXY',None); os.environ.pop('https_proxy',None)
from dotenv import load_dotenv
load_dotenv(r'D:\001Alpha\Hyper-Alpha-Arena\.env', override=True)
from sqlalchemy import create_engine, text
eng = create_engine('postgresql+psycopg://db_admin:YOUR_DB_PASSWORD@localhost:5432/alpha_arena')
with eng.connect() as c:
    c.execute(text("SET app.is_admin='on'"))
    row = c.execute(text("SELECT model, base_url, api_key FROM llm_configurations WHERE id=17")).fetchone()
model, base_url, api_key = row
import httpx
messages=[{"role":"system","content":"只返回JSON，不要额外文字。"},{"role":"user","content":"用一句话判断 BTC 当前应该 buy/hold/sell，输出 {\"action\":\"hold\",\"confidence\":50}"}]
payload = {"model": model, "messages": messages, "stream": True, "max_tokens": 2000, "thinking":{"type":"enabled"},"reasoning_effort":"high"}
content=''; reasoning=''
shape_counts={}
first_shape=None
t0=time.time()
with httpx.Client(timeout=120) as client:
    with client.stream("POST", base_url.rstrip('/')+'/chat/completions', headers={"Authorization":"Bearer "+api_key}, json=payload) as r:
        print('status', r.status_code)
        if r.status_code!=200:
            print(r.read().decode()[:500]); raise SystemExit
        for line in r.iter_lines():
            if not line or not line.startswith('data:'): continue
            js=line[5:].strip()
            if js=='[DONE]': print('DONE at', time.time()-t0); break
            try: d=json.loads(js)
            except: continue
            choices=d.get('choices') or []
            if not choices: continue
            delta=choices[0].get('delta') or {}
            keys=tuple(sorted(k for k in delta.keys() if delta.get(k)))
            if first_shape is None: first_shape=keys
            shape_counts[keys]=shape_counts.get(keys,0)+1
            c2=delta.get('content')
            rc=delta.get('reasoning_content')
            if isinstance(c2,str): content+=c2
            if isinstance(rc,str): reasoning+=rc
print('elapsed', round(time.time()-t0,1))
print('content_len', len(content), 'reasoning_len', len(reasoning))
print('delta shapes seen:')
for k,v in shape_counts.items(): print('  ', k, v)
print('content=', (content[:200] if content else '(EMPTY)'))
print('reasoning_head=', (reasoning[:200] if reasoning else '(EMPTY)'))
