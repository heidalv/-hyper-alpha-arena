"""P1 验收测试：LPM 杠杆 min + tier 映射 + 部分平仓（真实DB临时账户）。
通过标准: 断言全过。
"""
import sys

sys.path.insert(0, ".")

import psycopg2
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

MAIN_DSN = "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena"

from backend.services.live_position_manager import (
    LivePositionManager,
    tier_to_nature,
)

lpm = LivePositionManager()

raw = psycopg2.connect(host="127.0.0.1", port=5432, dbname="alpha_arena",
                       user="laobao", password="alpha_pass")
raw.autocommit = True
cur = raw.cursor()
cur.execute("SET app.is_admin='on'")
cur.execute("SELECT id FROM users ORDER BY id LIMIT 1")
uid = cur.fetchone()[0]
cur.execute(
    """INSERT INTO accounts (user_id, version, name, account_type, is_active,
       auto_trading_enabled, initial_capital, current_cash, frozen_cash,
       hyperliquid_enabled, binance_enabled, binance_testnet, trading_mode,
       selected_exchange)
       VALUES (%s, '1', '_lpm_test', 'paper', 'false', 'false', 500, 500, 0,
       'false', 'false', 'false', 'paper', 'binance') RETURNING id""",
    (uid,),
)
acct = cur.fetchone()[0]
print("temp account:", acct)

engine = create_engine(MAIN_DSN)
S = sessionmaker(bind=engine)
db = S()

orders = []


def fake_exchange(db_, symbol, order_side, qty, lev):
    orders.append((symbol, order_side, round(qty, 6), lev))
    return {"order_id": "o%d" % len(orders), "fill_price": 100.0}


def clean():
    cur.execute("DELETE FROM live_sub_positions WHERE account_id=%s", (acct,))
    cur.execute("DELETE FROM accounts WHERE id=%s", (acct,))


try:
    assert tier_to_nature("short") == "scalp"
    assert tier_to_nature("mid") == "swing"
    assert tier_to_nature("long") == "trend_follow"
    assert tier_to_nature("unknown") == "unknown"
    print("PASS G4 tier→nature 映射")

    lpm.execute_order(db, acct, "BTC", "long", 100.0, 10.0, "scalp", "short", fake_exchange)
    lpm.execute_order(db, acct, "BTC", "long", 50.0, 5.0, "trend_follow", "long", fake_exchange)
    v = lpm.get_net_position(db, acct, "BTC")
    assert v.net_side == "long" and abs(v.net_size - 150.0) < 1e-6, v
    assert v.unified_leverage == 5.0, "min 杠杆失败: %s" % v.unified_leverage
    assert orders[-1][2] == 50.0 and orders[-1][3] == 5.0
    print("PASS G1 杠杆取 min (unified_lev=%.1f)" % v.unified_leverage)

    lpm.execute_order(db, acct, "BTC", "long", 120.0, 8.0, "scalp", "short", fake_exchange)
    v = lpm.get_net_position(db, acct, "BTC")
    assert abs(v.net_size - 170.0) < 1e-6
    assert orders[-1][2] == 20.0
    assert v.unified_leverage == 5.0
    print("PASS 同nature替换+净差额单 (delta=+20)")

    rr = lpm.reduce_sub_position(db, acct, "BTC", "scalp", 0.5, fake_exchange)
    assert rr["reduced"] and abs(rr["reduced_qty"] - 60.0) < 1e-6
    v = lpm.get_net_position(db, acct, "BTC")
    assert abs(v.net_size - 110.0) < 1e-6, v.net_size
    assert orders[-1][1] == "sell" and abs(orders[-1][2] - 60.0) < 1e-6
    print("PASS G3 部分平仓 (reduce 50%% -> 净仓110)")

    rc = lpm.close_sub_position(db, acct, "BTC", "trend_follow", fake_exchange)
    assert rc["closed"] and abs(rc["closed_size"] - 50.0) < 1e-6
    v = lpm.get_net_position(db, acct, "BTC")
    assert abs(v.net_size - 60.0) < 1e-6
    print("PASS 分层平仓映射")

    # 同nature替换语义: scalp 多60 → scalp 空80 替换 = 净 -80（差额单 sell 140）
    lpm.execute_order(db, acct, "BTC", "short", 80.0, 5.0, "scalp", "short", fake_exchange)
    v = lpm.get_net_position(db, acct, "BTC")
    assert v.net_side == "short" and abs(v.net_size + 80.0) < 1e-6, v.net_size
    assert abs(orders[-1][2] - 140.0) < 1e-6
    print("PASS 反向净仓翻向 (net=-80, delta=sell 140)")

    ok = lpm.reconcile(db, acct, "BTC", -80.0, 5.0)
    assert ok["matched"]
    bad = lpm.reconcile(db, acct, "BTC", -78.0, 5.0)
    assert not bad["matched"]
    print("PASS reconcile 检测")

    print("\n=== P1 全部通过 ===")
finally:
    db.close()
    clean()
    raw.close()
