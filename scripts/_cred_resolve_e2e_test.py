# -*- coding: utf-8 -*-
"""E2E: 实盘下单路径的账户级凭证解析(2026-08-28) — 重试版
建临时实盘账户+绑定凭证 → 独立进程调 exchange_manager.get_or_create_global_client
验证: 带 account_id 命中账户级; 不带命中全局; 无凭证的其它交易所返回 None。
"""
import json
import time
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"


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


st, data = api("POST", "/api/account/", {
    "name": "_e2e_resolve", "trading_mode": "live", "selected_exchange": "binance",
})
acct_id = data.get("id")
assert st == 200 and acct_id, (st, data)
print("created live account", acct_id)

st, data = api("POST", "/api/exchange/credentials", {
    "exchange": "binance", "label": "resolve测试", "api_key": "rk_test_123456",
    "api_secret": "rs_test_secret", "account_id": acct_id, "testnet": False, "enabled": True,
})
cred_id = data.get("id")
assert st == 200 and cred_id, (st, data)
print("created bound credential", cred_id)

# 独立进程验证解析(模拟 trading_commands/live_executor 的调用)
import subprocess
code = '''
import sys
sys.path.insert(0, ".")
from backend.services.exchange.exchange_manager import ExchangeManager
m = ExchangeManager()
a = m.get_or_create_global_client("binance", user_id=326, account_id=%d)
print("ACCT_LEVEL:", a is not None)
g = m.get_or_create_global_client("binance", user_id=326, account_id=0)
print("GLOBAL_LEVEL:", g is not None)
n = m.get_or_create_global_client("bybit", user_id=326, account_id=0)
print("NONE_FOR_BYBIT:", n is None)
''' % acct_id
out = subprocess.run(
    [r".venv/Scripts/python.exe", "-c", code],
    capture_output=True, text=True, timeout=240, cwd=".",
)
print(out.stdout[-800:])
if out.stderr.strip():
    print("STDERR_TAIL:", out.stderr.strip()[-800:])

# 清理(含上次遗留的 184)
for _a in (184,):
    try:
        api("DELETE", f"/api/account/{_a}?hard=true")
    except Exception:
        pass
api("DELETE", f"/api/exchange/credentials/{cred_id}")
api("DELETE", f"/api/account/{acct_id}?hard=true")
print("cleaned up")
