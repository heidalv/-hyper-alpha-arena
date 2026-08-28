# -*- coding: utf-8 -*-
"""验收: 本地单租户模式下,回环请求携带无效JWT时写操作按无凭证放行(2026-08-28)
复现浏览器现场: DELETE 携带过期 Bearer → 旧行为 401 → 新行为 200。
"""
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"

GARBAGE_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiIzMjYiLCJ0eXBlIjoiYWNjZXNzIiwiZXhwIjoxNzAwMDAwMDAwfQ."
    "invalid-signature-bytes"
)


def api(method, path, body=None, headers=None, timeout=30):
    req = urllib.request.Request(BASE + path, method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]


fails = []


def check(name, cond, info):
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}: {info}")
    if not cond:
        fails.append(name)


# 0) 无凭证读(页面能加载的根基,顺带验证 AUTH_LOCAL_TENANT 已生效)
st, data = api("GET", "/api/account")
check("GET /api/account (no creds)", st == 200, f"status={st}")

# 1) 建临时账户 A(无凭证 —— 旧行为下写操作本就会 401)
st, data = api("POST", "/api/account/", {
    "name": "_authfb_test_A", "trading_mode": "paper",
    "initial_capital": 500, "selected_exchange": "binance",
})
aid_a = data.get("id") if isinstance(data, dict) else None
check("POST create account A (no creds)", st == 200 and aid_a, f"status={st}")

# 2) 浏览器现场: DELETE 携带无效 Bearer → 期望 200(修复前是 401)
st, data = api("DELETE", f"/api/account/{aid_a}",
               headers={"Authorization": f"Bearer {GARBAGE_JWT}"})
check("DELETE with INVALID Bearer (loopback)", st == 200, f"status={st} body={data if st!=200 else ''}")

# 3) 同一账户 hard=true 彻底删除,同样携带无效 Bearer → 期望 200
st, data = api("DELETE", f"/api/account/{aid_a}?hard=true",
               headers={"Authorization": f"Bearer {GARBAGE_JWT}"})
check("DELETE hard=true with INVALID Bearer", st == 200, f"status={st}")

# 4) 对照: 无凭证 DELETE → 期望 200(本地租户通道,旧行为即如此)
st, data = api("POST", "/api/account/", {
    "name": "_authfb_test_B", "trading_mode": "paper",
    "initial_capital": 500, "selected_exchange": "binance",
})
aid_b = data.get("id") if isinstance(data, dict) else None
st, data = api("DELETE", f"/api/account/{aid_b}")
check("DELETE with NO creds (control)", st == 200, f"status={st}")

# 5) 危险读 GET 携带无效 Bearer → 旧行为 401,新行为放行(llm-configs 前缀)
st, data = api("GET", "/api/llm-configs",
               headers={"Authorization": f"Bearer {GARBAGE_JWT}"})
check("GET /api/llm-configs with INVALID Bearer", st == 200, f"status={st}")

print("=" * 40)
print("RESULT:", "ALL PASS" if not fails else f"FAILED: {fails}")
