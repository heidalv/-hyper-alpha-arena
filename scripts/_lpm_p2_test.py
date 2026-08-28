"""P2 验收：对账任务 mismatches 告警轮次 + 自动对齐（scale / close_all）。
通过标准: 断言全过。
"""
import sys

sys.path.insert(0, ".")

import psycopg2
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

MAIN_DSN = "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena"

from backend.services.live_position_manager import live_position_manager as lpm
from backend.services.live_position_reconciler import (
    run_reconcile_once,
    _align_ledger_to_exchange,
)

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
       VALUES (%s, '1', '_lpm_p2_test', 'paper', 'false', 'false', 500, 500, 0,
       'false', 'false', 'false', 'paper', 'binance') RETURNING id""",
    (uid,),
)
acct = cur.fetchone()[0]
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
    # 本地: BTC 多 100(scalp 10x) + 50(trend 5x) → net 150
    lpm.execute_order(db, acct, "BTC", "long", 100.0, 10.0, "scalp", "short", fake_exchange)
    lpm.execute_order(db, acct, "BTC", "long", 50.0, 5.0, "trend_follow", "long", fake_exchange)

    # 交易所实仓: 145 (差 5) → 第1轮 mismatch 不修
    r1 = run_reconcile_once(db, acct, lambda aid: [{"symbol": "BTC", "net_qty": 145.0, "leverage": 5.0}])
    rec = [x for x in r1["results"] if x["symbol"] == "BTC"][0]
    assert not rec["matched"] and rec["rounds"] == 1 and rec["auto_fix"] is None
    v = lpm.get_net_position(db, acct, "BTC")
    assert abs(v.net_size - 150.0) < 1e-6  # 未修
    print("PASS P2 第1轮 mismatch 告警不修 (rounds=1, 本地仍150)")

    # 第2轮仍差 → 自动对齐 scale: 150→145, 因子 0.9667
    r2 = run_reconcile_once(db, acct, lambda aid: [{"symbol": "BTC", "net_qty": 145.0, "leverage": 5.0}])
    rec2 = [x for x in r2["results"] if x["symbol"] == "BTC"][0]
    assert not rec2["matched"] and rec2["auto_fix"] and rec2["auto_fix"]["mode"] == "scale"
    v = lpm.get_net_position(db, acct, "BTC")
    assert abs(v.net_size - 145.0) < 1e-6
    print("PASS P2 连续2轮自动对齐 scale (net 150→145)")

    # 恢复一致 → matched
    r3 = run_reconcile_once(db, acct, lambda aid: [{"symbol": "BTC", "net_qty": 145.0, "leverage": 5.0}])
    rec3 = [x for x in r3["results"] if x["symbol"] == "BTC"][0]
    assert rec3["matched"]
    print("PASS P2 恢复一致 matched")

    # 交易所 flat → 连续2轮后 close_all
    run_reconcile_once(db, acct, lambda aid: [{"symbol": "BTC", "net_qty": 0.0, "leverage": 1.0}])
    run_reconcile_once(db, acct, lambda aid: [{"symbol": "BTC", "net_qty": 0.0, "leverage": 1.0}])
    v = lpm.get_net_position(db, acct, "BTC")
    assert v.net_side == "flat"
    print("PASS P2 交易所flat→本地关平 (close_all, 2轮)")

    # 方向冲突 → 不臆造需人工
    lpm.execute_order(db, acct, "ETH", "long", 30.0, 5.0, "scalp", "short", fake_exchange)
    run_reconcile_once(db, acct, lambda aid: [{"symbol": "ETH", "net_qty": -30.0, "leverage": 5.0}])
    r5 = run_reconcile_once(db, acct, lambda aid: [{"symbol": "ETH", "net_qty": -30.0, "leverage": 5.0}])
    rec5 = [x for x in r5["results"] if x["symbol"] == "ETH"][0]
    assert not rec5["matched"] and rec5["auto_fix"] and not rec5["auto_fix"]["fixed"]
    assert rec5["auto_fix"]["mode"] == "side_conflict"
    print("PASS P2 方向冲突不臆造 (side_conflict 需人工)")

    print("\n=== P2 全部通过 ===")
finally:
    db.close()
    clean()
    raw.close()
