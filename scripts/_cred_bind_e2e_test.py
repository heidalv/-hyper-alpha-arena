# -*- coding: utf-8 -*-
"""E2E: 账户↔API凭证 绑定闭环(2026-08-28)
真实 API: 建实盘账户 → 建凭证并绑定 → 列表核对 → 解绑 → 重绑 → 清理。
"""
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"


def api(method, path, body=None, timeout=45):
    req = urllib.request.Request(BASE + path, method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


fails = []


def check(name, cond, info):
    print(("[PASS] " if cond else "[FAIL] ") + name + ": " + str(info))
    if not cond:
        fails.append(name)


# 1) 建实盘账户
st, data = api("POST", "/api/account/", {
    "name": "_e2e_live_bind", "trading_mode": "live",
    "selected_exchange": "binance",
})
acct_id = data.get("id") if isinstance(data, dict) else None
check("create live account", st == 200 and acct_id, f"status={st} id={acct_id}")

# 2) 建凭证并绑定到该账户
st, data = api("POST", "/api/exchange/credentials", {
    "exchange": "binance", "label": "e2e测试", "api_key": "e2e_test_key_123456789",
    "api_secret": "e2e_secret_abcdef", "account_id": acct_id,
    "testnet": False, "enabled": True,
})
cred_id = data.get("id") if isinstance(data, dict) else None
check("create credential bound to account", st == 200 and cred_id, f"status={st} {data}")

# 3) 按账户查凭证
st, data = api("GET", f"/api/exchange/credentials?account_id={acct_id}")
lst = data if isinstance(data, list) else []
hit = next((c for c in lst if c.get("id") == cred_id), None)
check("list by account_id", st == 200 and hit is not None, f"status={st} hit={hit}")
check("api_key_masked present", bool(hit and hit.get("api_key_masked")), f"masked={hit and hit.get('api_key_masked')}")

# 4) 解绑(全局)
st, data = api("PUT", f"/api/exchange/credentials/{cred_id}/bind", {"account_id": None})
check("unbind", st == 200 and data.get("account_id") is None, f"status={st} {data}")
st, data = api("GET", f"/api/exchange/credentials?account_id={acct_id}")
lst = data if isinstance(data, list) else []
check("list after unbind empty", st == 200 and not any(c.get("id") == cred_id for c in lst), f"status={st}")

# 5) 重绑
st, data = api("PUT", f"/api/exchange/credentials/{cred_id}/bind", {"account_id": acct_id})
check("rebind", st == 200 and data.get("account_id") == acct_id, f"status={st} {data}")

# 6) 清理: 删凭证 + 硬删账户
st, data = api("DELETE", f"/api/exchange/credentials/{cred_id}")
check("cleanup delete credential", st == 200, f"status={st}")
st, data = api("DELETE", f"/api/account/{acct_id}?hard=true")
check("cleanup hard delete account", st == 200, f"status={st} {data if st != 200 else ''}")

print("=" * 40)
print("RESULT:", "ALL PASS" if not fails else f"FAILED: {fails}")
