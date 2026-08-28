"""P4 全场景演练：三周期同币交叉开平 + 虚拟子仓对账零漂移。
场景:
 A. 同币同向三层叠加(scalp 10x / swing 8x / trend 5x) → 净仓=Σ, 杠杆=min
 B. scalp 分层分批止盈(35%/35%) → 部分平仓差额
 C. 中线反向(多→空) → 净仓翻向差额单
 D. 长线全平 → 余仓=scalp残余
 E. 全程交易所模拟净仓 vs 本地Σ 对账 matched
通过标准: 断言全过。
"""
import sys

sys.path.insert(0, ".")

import psycopg2
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

MAIN_DSN = "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena"

from backend.services.live_position_manager import live_position_manager as lpm
from backend.services.live_position_reconciler import run_reconcile_once

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
       VALUES (%s, '1', '_lpm_p4_test', 'paper', 'false', 'false', 500, 500, 0,
       'false', 'false', 'false', 'paper', 'binance') RETURNING id""",
    (uid,),
)
acct = cur.fetchone()[0]
engine = create_engine(MAIN_DSN)
S = sessionmaker(bind=engine)
db = S()

# 交易所模拟净仓（fake exchange 的真实账）
ex_net = {}


def fake_exchange(db_, symbol, order_side, qty, lev):
    d = qty if order_side == "buy" else -qty
    ex_net[symbol] = round(ex_net.get(symbol, 0.0) + d, 8)
    return {"order_id": "o%d" % len(ex_net), "fill_price": 100.0}


def check_invariant(symbol, tag):
    v = lpm.get_net_position(db, acct, symbol)
    assert abs(v.net_size - ex_net.get(symbol, 0.0)) < 1e-6, (
        "%s 漂移: 本地=%s 交易所=%s" % (tag, v.net_size, ex_net.get(symbol, 0.0))
    )
    rec = run_reconcile_once(db, acct,
                             lambda aid, s=symbol: [{"symbol": s, "net_qty": ex_net.get(s, 0.0), "leverage": 1.0}])
    m = [x for x in rec["results"] if x["symbol"] == symbol][0]
    assert m["matched"], "%s 对账未过" % tag
    return v


def clean():
    cur.execute("DELETE FROM live_sub_positions WHERE account_id=%s", (acct,))
    cur.execute("DELETE FROM accounts WHERE id=%s", (acct,))


try:
    # A. 同币同向三层叠加
    lpm.execute_order(db, acct, "SOL", "long", 100.0, 10.0, "scalp", "short", fake_exchange)
    lpm.execute_order(db, acct, "SOL", "long", 80.0, 8.0, "swing", "mid", fake_exchange)
    lpm.execute_order(db, acct, "SOL", "long", 120.0, 5.0, "trend_follow", "long", fake_exchange)
    v = check_invariant("SOL", "A三层叠加")
    assert abs(v.net_size - 300.0) < 1e-6
    assert v.unified_leverage == 5.0
    print("PASS A 三层同向叠加 (net=300, lev=min=5)")

    # B. scalp 分批止盈 35% × 2
    r1 = lpm.reduce_sub_position(db, acct, "SOL", "scalp", 0.35, fake_exchange)
    r2 = lpm.reduce_sub_position(db, acct, "SOL", "scalp", 0.35, fake_exchange)
    check_invariant("SOL", "B分批止盈")
    assert abs(r1["reduced_qty"] - 35.0) < 1e-6 and abs(r2["reduced_qty"] - 22.75) < 1e-6
    print("PASS B scalp 35%%×2 分批止盈 (qty 35 + 22.75)")

    # C. 中线反向: swing 多80残余... 实际 swing 未动=80, 改 swing 空 60
    lpm.execute_order(db, acct, "SOL", "short", 60.0, 8.0, "swing", "mid", fake_exchange)
    v = check_invariant("SOL", "C中线反向")
    print("PASS C 中线反向 (net=%.1f)" % v.net_size)

    # D. 长线全平
    rc = lpm.close_sub_position(db, acct, "SOL", "trend_follow", fake_exchange)
    v = check_invariant("SOL", "D长线全平")
    print("PASS D 长线全平 (net=%.1f)" % v.net_size)

    # E. 跨币隔离: BTC 短线 + ETH 趋势 互不影响
    lpm.execute_order(db, acct, "BTC", "long", 50.0, 10.0, "scalp", "short", fake_exchange)
    lpm.execute_order(db, acct, "ETH", "long", 40.0, 5.0, "trend_follow", "long", fake_exchange)
    v_btc = check_invariant("BTC", "E-BTC")
    v_eth = check_invariant("ETH", "E-ETH")
    assert abs(v_btc.net_size - 50.0) < 1e-6 and abs(v_eth.net_size - 40.0) < 1e-6
    print("PASS E 跨币隔离 (BTC=50, ETH=40)")

    print("\n=== P4 全场景演练通过：交易所净仓 vs 本地Σ 全程零漂移 ===")
finally:
    db.close()
    clean()
    raw.close()
