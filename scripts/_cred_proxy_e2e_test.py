# -*- coding: utf-8 -*-
"""E2E: 凭证级代理配置(币安IP白名单出口) 2026-08-28
建凭证(带 proxy_url) → 列表核对 → 独立进程验证 ccxt 客户端拿到 socksProxy → 清理。
"""
import json
import time
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"
PROXY = "socks5://127.0.0.1:1080"


def api(method, path, body=None, timeout=120, tries=3):
    last = None
    for i in range(tries):
        req = urllib.request.Request(BASE + path, method=method)
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()[:200]
        except Exception as e:
            last = e
            time.sleep(5)
    raise last


fails = []


def check(name, cond, info):
    print(("[PASS] " if cond else "[FAIL] ") + name + ": " + str(info))
    if not cond:
        fails.append(name)


st, data = api("POST", "/api/exchange/credentials", {
    "exchange": "binance", "label": "proxy测试", "api_key": "pk_test_123456",
    "api_secret": "ps_test_secret", "testnet": False, "enabled": True,
    "proxy_url": PROXY,
})
cred_id = data.get("id") if isinstance(data, dict) else None
check("create credential with proxy_url", st == 200 and cred_id, f"status={st} {data}")

st, data = api("GET", "/api/exchange/credentials")
lst = data if isinstance(data, list) else []
hit = next((c for c in lst if c.get("id") == cred_id), None)
check("list returns proxy_url", bool(hit and hit.get("proxy_url") == PROXY), f"hit={hit}")

# 独立进程: 客户端解析后代理应落到 ccxt socksProxy
import subprocess
code = '''
import sys
sys.path.insert(0, ".")
from backend.services.exchange.exchange_manager import ExchangeManager
m = ExchangeManager()
c = m.get_or_create_global_client("binance", user_id=326, account_id=0)
ex = getattr(c, "_exchange", None)
print("CLIENT:", ex is not None)
if ex is not None:
    print("SOCKS_PROXY:", getattr(ex, "socksProxy", None))
    print("HTTP_PROXIES:", getattr(ex, "proxies", None))
'''
out = subprocess.run(
    [r".venv/Scripts/python.exe", "-c", code],
    capture_output=True, text=True, timeout=240, cwd=".",
)
print(out.stdout[-600:])
if out.stderr.strip():
    print("STDERR_TAIL:", out.stderr.strip()[-400:])
if "SOCKS_PROXY: %s" % PROXY not in out.stdout:
    fails.append("client proxy not applied")

api("DELETE", f"/api/exchange/credentials/{cred_id}")
print("cleaned up cred", cred_id)
print("=" * 40)
print("RESULT:", "ALL PASS" if not fails else f"FAILED: {fails}")
