"""P1 验收：删除分级（软删停会话 / 硬删守卫）。
真实API调用（本地urllib），临时账户+临时会话，验证后清理。
"""
import json
import sys
import urllib.request

sys.path.insert(0, ".")

import psycopg2

BASE = "http://127.0.0.1:8000"


def api(method, path, body=None, timeout=30):
    req = urllib.request.Request(BASE + path, method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


raw = psycopg2.connect(host="127.0.0.1", port=5432, dbname="alpha_arena",
                       user="laobao", password="alpha_pass")
raw.autocommit = True
cur = raw.cursor()
cur.execute("SET app.is_admin='on'")
cur.execute("SELECT id FROM users ORDER BY id LIMIT 1")
uid = cur.fetchone()[0]

try:
    # 1) 建临时账户
    r = api("POST", "/api/account/", {
        "name": "_p1_del_test", "trading_mode": "paper",
        "initial_capital": 500, "selected_exchange": "binance",
    })
    acct_id = r["id"] if "id" in r else r.get("account_id")
    print("created account:", acct_id, r)

    # 2) 造一个 running 会话绑定该账户
    cur.execute("""
        INSERT INTO full_auto_sessions (session_id, account_id, paper_account_id,
        trading_mode, status, symbols, risk_level, started_at)
        VALUES ('_p1_sess_test', %s, %s, 'paper', 'running',
        '["BTC"]', 'moderate', now()) RETURNING id
    """, (acct_id, acct_id))
    sess_row = cur.fetchone()
    print("session row:", sess_row)

    # 3) 软删 → 会话应被停
    r2 = api("DELETE", f"/api/account/{acct_id}")
    print("soft delete:", r2)
    assert r2.get("deleted") is False
    assert "_p1_sess_test" in (r2.get("stopped_sessions") or [])
    cur.execute("SELECT status FROM full_auto_sessions WHERE session_id='_p1_sess_test'")
    s_status = cur.fetchone()
    assert s_status[0] == "stopped", s_status
    cur.execute("SELECT is_active FROM accounts WHERE id=%s", (acct_id,))
    assert cur.fetchone()[0] == "false"
    print("PASS P1 软删: 会话已停(status=stopped), 账户is_active=false")

    # 4) 硬删（无持仓）→ 配置级清除 + 匿名化（行保留满足FK审计）
    r3 = api("DELETE", f"/api/account/{acct_id}?hard=true")
    print("hard delete:", r3)
    assert r3.get("deleted") is True
    cur.execute("SELECT name, is_active FROM accounts WHERE id=%s", (acct_id,))
    _row = cur.fetchone()
    assert _row[1] == "false" and str(_row[0]).startswith("_deleted_"), _row
    print("PASS P1 硬删: 配置清除+匿名化(名字释放可复用)")

    # 5) 有持仓的硬删守卫
    r4 = api("POST", "/api/account/", {
        "name": "_p1_del_test2", "trading_mode": "paper",
        "initial_capital": 500, "selected_exchange": "binance",
    })
    acct2 = r4.get("id") or r4.get("account_id")
    cur.execute("""INSERT INTO paper_positions
        (account_id, symbol, side, size, entry_price, margin, leverage, status, trade_nature)
        VALUES (%s, 'BTC', 'long', 0.01, 80000, 100, 10, 'open', 'scalp')""", (acct2,))
    try:
        api("DELETE", f"/api/account/{acct2}?hard=true")
        raise AssertionError("硬删应被409拒绝")
    except urllib.error.HTTPError as e:
        assert e.code == 409, e.code
        print("PASS P1 硬删守卫: 有open持仓返回409")
    # 清理
    cur.execute("DELETE FROM paper_positions WHERE account_id=%s", (acct2,))
    cur.execute("DELETE FROM accounts WHERE id=%s", (acct2,))

    print("\n=== P1 全部通过 ===")
finally:
    cur.execute("DELETE FROM full_auto_sessions WHERE session_id='_p1_sess_test'")
    cur.execute("DELETE FROM account_asset_snapshots WHERE account_id IN (SELECT id FROM accounts WHERE name LIKE '_p1_del_test%' OR name LIKE '_deleted_%_p1_del_test%')")
    cur.execute("DELETE FROM accounts WHERE name LIKE '_p1_del_test%' OR name LIKE '_deleted_%_p1_del_test%'")
    raw.close()
