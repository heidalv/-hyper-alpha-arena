import os, sys, json, time
sys.path.insert(0, r'D:\001Alpha\Hyper-Alpha-Arena')
os.environ['NO_PROXY']='127.0.0.1,localhost'; os.environ['no_proxy']='127.0.0.1,localhost'
os.environ.pop('HTTP_PROXY',None); os.environ.pop('http_proxy',None); os.environ.pop('HTTPS_PROXY',None); os.environ.pop('https_proxy',None)
from dotenv import load_dotenv
load_dotenv(r'D:\001Alpha\Hyper-Alpha-Arena\.env', override=True)
from sqlalchemy import create_engine, text
eng = create_engine('postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena')
with eng.connect() as c:
    c.execute(text("SET app.is_admin='on'"))
    row = c.execute(text("SELECT model, base_url, api_key FROM llm_configurations WHERE id=17")).fetchone()
model, base_url, api_key = row
print('CONFIG', model, base_url, 'key_len', len(api_key or ''))

import httpx
messages=[{"role":"system","content":"只返回JSON，不要额外文字。"},{"role":"user","content":"用一句话判断 BTC 当前应该 buy/hold/sell，输出 {\"action\":\"hold\",\"confidence\":50}"}]

def test(tag, extra):
    payload = {"model": model, "messages": messages, "stream": False, "max_tokens": 2000}
    payload.update(extra)
    t0=time.time()
    try:
        with httpx.Client(timeout=120) as client:
            r = client.post(base_url.rstrip('/')+'/chat/completions', headers={"Authorization":"Bearer "+api_key}, json=payload)
        dt=time.time()-t0
        print(f'--- {tag} --- status={r.status_code} elapsed={dt:.1f}s')
        if r.status_code!=200:
            print('BODY', r.text[:500]); return
        j=r.json()
        msg=j.get('choices',[{}])[0].get('message',{})
        content=msg.get('content') or ''
        rc=msg.get('reasoning_content') or ''
        print('content_len=', len(content), 'reasoning_len=', len(rc))
        print('content=', (content[:300] if content else '(EMPTY)'))
        print('reasoning_head=', (rc[:200] if rc else '(EMPTY)'))
        print('usage=', j.get('usage'))
    except Exception as e:
        print(f'--- {tag} --- EXC {e}')

test('no thinking params', {})
test('thinking enabled + effort high', {"thinking":{"type":"enabled"},"reasoning_effort":"high"})
test('thinking enabled + effort max', {"thinking":{"type":"enabled"},"reasoning_effort":"max"})
test('thinking disabled', {"thinking":{"type":"disabled"}})
