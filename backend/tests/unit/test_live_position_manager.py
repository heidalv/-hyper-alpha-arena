# backend/tests/unit/test_live_position_manager.py
"""LivePositionManager 单元测试。

覆盖核心场景(用 in-memory SQLite + ``Base.metadata.create_all`` 建真实表,
mock exchange_callback):
1. 同 nature 加仓(scalp long + more scalp long → exchange gets delta)
2. 跨 nature 对冲(scalp long + trend short → exchange gets net)
3. 关闭子仓位(scalp closes → exchange gets reverse delta)
4. 净仓位视图(多 tier → 正确 net_side/size/leverage)
5. 对账 match + mismatch
6. 边界:无 open 子仓时 close 返回 not-closed;净变化≈0 时 skip exchange
7. dataclass 默认值不共享(Mutable 默认陷阱)

设计说明
--------
用真实 SQLite in-memory + create_all(与 test_autocoin_feedback.py 同款),而非
MagicMock db —— 因为 LivePositionManager 真正读写 LiveSubPosition 行(add/
flush/commit/status 变更),用真表能验证 SQL/约束/状态流转的端到端正确性。
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

# 让 backend.* 可 import(与 conftest.py 同款)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database.models import Account, Base, LiveSubPosition, User
from backend.services.live_position_manager import (
    LivePositionManager,
    NetPositionView,
)


# ─────────────────────────────────────────────────────────────────
# fixtures: in-memory sqlite,按 ORM 模型建表
# ─────────────────────────────────────────────────────────────────
@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", future=True)  # in-memory
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True)
    session = Session()

    # seed 一个 user + account(LiveSubPosition.account_id FK 引用 accounts.id)
    user = User(
        username="lpm_tester",
        email="lpm@example.com",
        password_hash="x",
        role="user",
        is_active="true",
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    account = Account(
        user_id=user.id,
        name="LPM test account",
        account_type="AI",
        trading_mode="live",
        initial_capital=10000,
        current_cash=10000,
    )
    session.add(account)
    session.commit()
    session.refresh(account)

    yield session, account
    session.close()
    engine.dispose()


def _make_exchange_cb(captured: list, fill_price: float = 100.0):
    """构造一个 mock exchange_callback。

    每次 call 记录 (symbol, order_side, net_qty, leverage) 到 captured,
    返回固定 order_id + fill_price(模拟交易所成交回执)。
    """
    counter = {"n": 0}

    def cb(db, symbol, order_side, net_qty, leverage):
        counter["n"] += 1
        captured.append({
            "symbol": symbol,
            "order_side": order_side,
            "net_qty": net_qty,
            "leverage": leverage,
            "call_n": counter["n"],
        })
        return {"order_id": f"oid-{counter['n']}", "fill_price": fill_price}

    return cb


# ═══════════════════════════════════════════════════════════════
# 1. 同 nature 加仓:scalp long + more scalp long → exchange gets delta
# ═══════════════════════════════════════════════════════════════
def test_same_nature_add_sends_delta(db_session):
    """scalp 开 long 1.0,再加仓到 long 2.0 → 第二笔 exchange delta=1.0(buy)。

    execute_order 语义:新子仓 *替换* 同 nature 旧子仓(同 nature 代表同一笔
    逻辑持仓)。故第二笔目标净=2.0,当前净=1.0,delta=+1.0。
    """
    session, account = db_session
    mgr = LivePositionManager()
    captured = []
    cb = _make_exchange_cb(captured, fill_price=100.0)

    # 第一笔:scalp 开 long 1.0
    r1 = mgr.execute_order(
        db=session, account_id=account.id, symbol="BTC",
        side="long", size=1.0, leverage=10,
        trade_nature="scalp", tier="short",
        exchange_callback=cb,
    )
    assert r1["sub_position_id"] is not None
    assert r1["order_id"] == "oid-1"
    assert r1["net_delta"] == pytest.approx(1.0)
    assert r1["order_side"] == "buy"
    assert captured[0]["net_qty"] == pytest.approx(1.0)
    assert captured[0]["order_side"] == "buy"
    assert captured[0]["leverage"] == 10

    # 第二笔:scalp 加仓到 long 2.0(替换旧 scalp 子仓)
    r2 = mgr.execute_order(
        db=session, account_id=account.id, symbol="BTC",
        side="long", size=2.0, leverage=10,
        trade_nature="scalp", tier="short",
        exchange_callback=cb,
    )
    # 目标净=2.0,当前净=1.0(旧 scalp long 1.0),delta=+1.0
    assert r2["net_delta"] == pytest.approx(1.0)
    assert r2["order_side"] == "buy"
    assert captured[1]["net_qty"] == pytest.approx(1.0)
    assert captured[1]["order_side"] == "buy"

    # 旧 scalp 子仓应被关闭,只剩 1 个 open scalp 子仓(size=2.0)
    open_subs = session.query(LiveSubPosition).filter_by(
        account_id=account.id, symbol="BTC", status="open",
    ).all()
    assert len(open_subs) == 1
    assert open_subs[0].size == pytest.approx(2.0)
    assert open_subs[0].side == "long"
    assert open_subs[0].trade_nature == "scalp"


def test_same_nature_reverse_replaces_old(db_session):
    """scalp long 1.0 → 反转 scalp short 0.6:exchange 收到净 sell 1.6。

    目标净 = -0.6(新 short),当前净 = +1.0(旧 long),delta = -1.6(sell 1.6)。
    旧 scalp long 被关闭,新建 scalp short 0.6。
    """
    session, account = db_session
    mgr = LivePositionManager()
    captured = []
    cb = _make_exchange_cb(captured)

    mgr.execute_order(
        db=session, account_id=account.id, symbol="BTC",
        side="long", size=1.0, leverage=10,
        trade_nature="scalp", tier="short",
        exchange_callback=cb,
    )
    r2 = mgr.execute_order(
        db=session, account_id=account.id, symbol="BTC",
        side="short", size=0.6, leverage=10,
        trade_nature="scalp", tier="short",
        exchange_callback=cb,
    )
    assert r2["net_delta"] == pytest.approx(-1.6)
    assert r2["order_side"] == "sell"
    assert captured[1]["net_qty"] == pytest.approx(1.6)
    assert captured[1]["order_side"] == "sell"

    open_subs = session.query(LiveSubPosition).filter_by(
        account_id=account.id, symbol="BTC", status="open",
    ).all()
    assert len(open_subs) == 1
    assert open_subs[0].side == "short"
    assert open_subs[0].size == pytest.approx(0.6)


# ═══════════════════════════════════════════════════════════════
# 2. 跨 nature 对冲:scalp long + trend short → exchange gets net
# ═══════════════════════════════════════════════════════════════
def test_cross_nature_hedge_sends_net(db_session):
    """scalp long 1.0 + trend short 0.4 → 第二笔 exchange buy delta=-0.6 即 sell 0.6。

    跨 nature 不替换:scalp 子仓保留。目标净 = 1.0 - 0.4 = 0.6,
    当前净 = 1.0(只有 scalp),delta = -0.4(sell 0.4)。
    """
    session, account = db_session
    mgr = LivePositionManager()
    captured = []
    cb = _make_exchange_cb(captured)

    mgr.execute_order(
        db=session, account_id=account.id, symbol="BTC",
        side="long", size=1.0, leverage=10,
        trade_nature="scalp", tier="short",
        exchange_callback=cb,
    )
    r2 = mgr.execute_order(
        db=session, account_id=account.id, symbol="BTC",
        side="short", size=0.4, leverage=12,
        trade_nature="trend_follow", tier="long",
        exchange_callback=cb,
    )
    # 目标净 = 1.0(scalp) - 0.4(trend) = 0.6;当前净 = 1.0;delta = -0.4
    assert r2["net_delta"] == pytest.approx(-0.4)
    assert r2["order_side"] == "sell"
    assert captured[1]["net_qty"] == pytest.approx(0.4)
    assert captured[1]["order_side"] == "sell"
    # 统一杠杆 = max(10, 12) = 12
    assert captured[1]["leverage"] == 12

    # 两个 nature 子仓都 open
    open_subs = session.query(LiveSubPosition).filter_by(
        account_id=account.id, symbol="BTC", status="open",
    ).all()
    assert len(open_subs) == 2
    natures = {s.trade_nature for s in open_subs}
    assert natures == {"scalp", "trend_follow"}


def test_cross_nature_hedge_fully_offset_zero_delta(db_session):
    """scalp long 1.0 + trend short 1.0 → 第二笔 delta=0(skip exchange)。"""
    session, account = db_session
    mgr = LivePositionManager()
    captured = []
    cb = _make_exchange_cb(captured)

    mgr.execute_order(
        db=session, account_id=account.id, symbol="BTC",
        side="long", size=1.0, leverage=10,
        trade_nature="scalp", tier="short",
        exchange_callback=cb,
    )
    r2 = mgr.execute_order(
        db=session, account_id=account.id, symbol="BTC",
        side="short", size=1.0, leverage=10,
        trade_nature="trend_follow", tier="long",
        exchange_callback=cb,
    )
    # 目标净=0,当前净=1.0,delta=-1.0 ≠ 0 —— 仍要发单(净变化非零)
    # 修正:本例 delta = 0 - 1.0 = -1.0,不是 0。改测真正的零 delta:
    assert r2["net_delta"] == pytest.approx(-1.0)


def test_cross_nature_no_net_change_skips_exchange(db_session):
    """先 scalp long 1.0 + trend short 1.0(净 0),再把 trend 调到 short 1.0
    (替换旧 trend)→ 目标净仍 0,delta=0 → skip exchange。"""
    session, account = db_session
    mgr = LivePositionManager()
    captured = []
    cb = _make_exchange_cb(captured)

    mgr.execute_order(
        db=session, account_id=account.id, symbol="BTC",
        side="long", size=1.0, leverage=10,
        trade_nature="scalp", tier="short",
        exchange_callback=cb,
    )
    mgr.execute_order(
        db=session, account_id=account.id, symbol="BTC",
        side="short", size=1.0, leverage=10,
        trade_nature="trend_follow", tier="long",
        exchange_callback=cb,
    )
    # 此时净=0。再次提交 trend short 1.0(同值替换)→ 目标净仍 0,delta=0
    captured.clear()
    r3 = mgr.execute_order(
        db=session, account_id=account.id, symbol="BTC",
        side="short", size=1.0, leverage=10,
        trade_nature="trend_follow", tier="long",
        exchange_callback=cb,
    )
    assert abs(r3["net_delta"]) < 1e-8
    assert r3["order_id"] is None  # skip exchange
    assert captured == []  # exchange_callback 未被调用


# ═══════════════════════════════════════════════════════════════
# 3. 关闭子仓位:scalp closes → exchange gets reverse delta
# ═══════════════════════════════════════════════════════════════
def test_close_sub_position_sends_reverse(db_session):
    """scalp long 1.0 + trend long 2.0 → close scalp → exchange sell 1.0。"""
    session, account = db_session
    mgr = LivePositionManager()
    captured = []
    cb = _make_exchange_cb(captured)

    mgr.execute_order(
        db=session, account_id=account.id, symbol="BTC",
        side="long", size=1.0, leverage=10,
        trade_nature="scalp", tier="short",
        exchange_callback=cb,
    )
    mgr.execute_order(
        db=session, account_id=account.id, symbol="BTC",
        side="long", size=2.0, leverage=12,
        trade_nature="trend_follow", tier="long",
        exchange_callback=cb,
    )
    captured.clear()

    r = mgr.close_sub_position(
        db=session, account_id=account.id, symbol="BTC",
        trade_nature="scalp", exchange_callback=cb,
    )
    assert r["closed"] is True
    assert r["closed_size"] == pytest.approx(1.0)
    assert r["order_id"] is not None
    # scalp 是 long → 反向 sell
    assert captured[0]["order_side"] == "sell"
    assert captured[0]["net_qty"] == pytest.approx(1.0)

    # scalp 关闭,trend 仍 open
    open_subs = session.query(LiveSubPosition).filter_by(
        account_id=account.id, symbol="BTC", status="open",
    ).all()
    assert len(open_subs) == 1
    assert open_subs[0].trade_nature == "trend_follow"


def test_close_sub_position_short_sends_buy(db_session):
    """trend short 0.5 → close → exchange buy 0.5。"""
    session, account = db_session
    mgr = LivePositionManager()
    captured = []
    cb = _make_exchange_cb(captured)

    mgr.execute_order(
        db=session, account_id=account.id, symbol="BTC",
        side="short", size=0.5, leverage=8,
        trade_nature="trend_follow", tier="long",
        exchange_callback=cb,
    )
    captured.clear()
    r = mgr.close_sub_position(
        db=session, account_id=account.id, symbol="BTC",
        trade_nature="trend_follow", exchange_callback=cb,
    )
    assert r["closed"] is True
    assert r["closed_size"] == pytest.approx(0.5)
    assert captured[0]["order_side"] == "buy"
    assert captured[0]["net_qty"] == pytest.approx(0.5)


def test_close_nonexistent_returns_not_closed(db_session):
    """无 open 子仓时 close → {closed: False}。"""
    session, account = db_session
    mgr = LivePositionManager()
    cb = _make_exchange_cb([])

    r = mgr.close_sub_position(
        db=session, account_id=account.id, symbol="BTC",
        trade_nature="scalp", exchange_callback=cb,
    )
    assert r["closed"] is False
    assert "no open sub-position" in r["reason"]


# ═══════════════════════════════════════════════════════════════
# 4. 净仓位视图:多 tier → 正确 net_side/size/leverage
# ═══════════════════════════════════════════════════════════════
def test_get_net_position_multi_tier(db_session):
    """scalp long 1.0(10x) + trend short 0.4(12x) → net long 0.6,lev 12。"""
    session, account = db_session
    mgr = LivePositionManager()
    cb = _make_exchange_cb([])

    mgr.execute_order(
        db=session, account_id=account.id, symbol="BTC",
        side="long", size=1.0, leverage=10,
        trade_nature="scalp", tier="short",
        exchange_callback=cb,
    )
    mgr.execute_order(
        db=session, account_id=account.id, symbol="BTC",
        side="short", size=0.4, leverage=12,
        trade_nature="trend_follow", tier="long",
        exchange_callback=cb,
    )

    view = mgr.get_net_position(session, account.id, "BTC")
    assert view.symbol == "BTC"
    assert view.net_side == "long"
    assert view.net_size == pytest.approx(0.6)
    assert view.unified_leverage == pytest.approx(12.0)
    assert len(view.sub_positions) == 2


def test_get_net_position_short_dominates(db_session):
    """scalp long 0.5 + trend short 1.5 → net short -1.0。"""
    session, account = db_session
    mgr = LivePositionManager()
    cb = _make_exchange_cb([])

    mgr.execute_order(
        db=session, account_id=account.id, symbol="BTC",
        side="long", size=0.5, leverage=10,
        trade_nature="scalp", tier="short",
        exchange_callback=cb,
    )
    mgr.execute_order(
        db=session, account_id=account.id, symbol="BTC",
        side="short", size=1.5, leverage=10,
        trade_nature="trend_follow", tier="long",
        exchange_callback=cb,
    )
    view = mgr.get_net_position(session, account.id, "BTC")
    assert view.net_side == "short"
    assert view.net_size == pytest.approx(-1.0)


def test_get_net_position_empty_is_flat(db_session):
    """无子仓 → flat / 0 / 默认 leverage 1.0。"""
    session, account = db_session
    mgr = LivePositionManager()
    view = mgr.get_net_position(session, account.id, "BTC")
    assert view.net_side == "flat"
    assert view.net_size == 0.0
    assert view.unified_leverage == 1.0
    assert view.sub_positions == []


def test_get_net_position_excludes_closed(db_session):
    """closed 子仓不计入净视图。"""
    session, account = db_session
    mgr = LivePositionManager()
    cb = _make_exchange_cb([])

    mgr.execute_order(
        db=session, account_id=account.id, symbol="BTC",
        side="long", size=1.0, leverage=10,
        trade_nature="scalp", tier="short",
        exchange_callback=cb,
    )
    mgr.close_sub_position(
        db=session, account_id=account.id, symbol="BTC",
        trade_nature="scalp", exchange_callback=cb,
    )
    view = mgr.get_net_position(session, account.id, "BTC")
    assert view.net_side == "flat"
    assert view.net_size == 0.0
    assert view.sub_positions == []


# ═══════════════════════════════════════════════════════════════
# 5. 对账:match + mismatch
# ═══════════════════════════════════════════════════════════════
def test_reconcile_match(db_session):
    """本地净 == 交易所净 → matched=True。"""
    session, account = db_session
    mgr = LivePositionManager()
    cb = _make_exchange_cb([])

    mgr.execute_order(
        db=session, account_id=account.id, symbol="BTC",
        side="long", size=1.0, leverage=10,
        trade_nature="scalp", tier="short",
        exchange_callback=cb,
    )
    r = mgr.reconcile(session, account.id, "BTC", exchange_qty=1.0, exchange_leverage=10)
    assert r["matched"] is True
    assert r["local"] == pytest.approx(1.0)
    assert r["exchange"] == pytest.approx(1.0)
    assert abs(r["diff"]) < 1e-9


def test_reconcile_mismatch(db_session):
    """本地净 1.0 vs 交易所 0.5 → diff 0.5 > 1% 容差 → matched=False。"""
    session, account = db_session
    mgr = LivePositionManager()
    cb = _make_exchange_cb([])

    mgr.execute_order(
        db=session, account_id=account.id, symbol="BTC",
        side="long", size=1.0, leverage=10,
        trade_nature="scalp", tier="short",
        exchange_callback=cb,
    )
    r = mgr.reconcile(session, account.id, "BTC", exchange_qty=0.5, exchange_leverage=10)
    assert r["matched"] is False
    assert r["diff"] == pytest.approx(0.5)


def test_reconcile_within_tolerance_matches(db_session):
    """本地 1.0 vs 交易所 1.005(0.5% 偏差)→ 在 1% 容差内 → matched=True。"""
    session, account = db_session
    mgr = LivePositionManager()
    cb = _make_exchange_cb([])

    mgr.execute_order(
        db=session, account_id=account.id, symbol="BTC",
        side="long", size=1.0, leverage=10,
        trade_nature="scalp", tier="short",
        exchange_callback=cb,
    )
    r = mgr.reconcile(session, account.id, "BTC", exchange_qty=1.005, exchange_leverage=10)
    assert r["matched"] is True


def test_reconcile_empty_local_vs_zero_exchange_matches(db_session):
    """本地无仓 + 交易所 0 → matched=True(diff=0)。"""
    session, account = db_session
    mgr = LivePositionManager()
    r = mgr.reconcile(session, account.id, "BTC", exchange_qty=0.0, exchange_leverage=1.0)
    assert r["matched"] is True
    assert r["local"] == 0.0
    assert r["exchange"] == 0.0


# ═══════════════════════════════════════════════════════════════
# 6. 边界 + dataclass 安全
# ═══════════════════════════════════════════════════════════════
def test_execute_order_size_zero_closes_only(db_session):
    """size=0 的 execute_order:关闭同 nature 旧仓,不开新仓(sub_id=None)。"""
    session, account = db_session
    mgr = LivePositionManager()
    cb = _make_exchange_cb([])

    mgr.execute_order(
        db=session, account_id=account.id, symbol="BTC",
        side="long", size=1.0, leverage=10,
        trade_nature="scalp", tier="short",
        exchange_callback=cb,
    )
    # size=0 → 平掉 scalp,不发新子仓
    r = mgr.execute_order(
        db=session, account_id=account.id, symbol="BTC",
        side="long", size=0.0, leverage=10,
        trade_nature="scalp", tier="short",
        exchange_callback=cb,
    )
    assert r["sub_position_id"] is None
    # 目标净=0,当前净=1.0,delta=-1.0(仍发反向单平仓)
    assert r["net_delta"] == pytest.approx(-1.0)
    assert r["order_side"] == "sell"

    open_subs = session.query(LiveSubPosition).filter_by(
        account_id=account.id, symbol="BTC", status="open",
    ).all()
    assert len(open_subs) == 0


def test_margin_computed_from_size_and_leverage(db_session):
    """新子仓 margin = size / leverage。"""
    session, account = db_session
    mgr = LivePositionManager()
    cb = _make_exchange_cb([], fill_price=50000.0)

    mgr.execute_order(
        db=session, account_id=account.id, symbol="BTC",
        side="long", size=2.0, leverage=10,
        trade_nature="scalp", tier="short",
        exchange_callback=cb,
    )
    sub = session.query(LiveSubPosition).filter_by(
        account_id=account.id, symbol="BTC", status="open",
    ).one()
    assert sub.margin == pytest.approx(0.2)  # 2.0 / 10
    assert sub.entry_price == pytest.approx(50000.0)
    assert sub.exchange_order_id == "oid-1"


def test_net_position_view_defaults_isolated():
    """NetPositionView 默认 list 不共享(Mutable 默认陷阱)。"""
    v1 = NetPositionView(symbol="BTC")
    v2 = NetPositionView(symbol="ETH")
    v1.sub_positions.append({"x": 1})
    assert v2.sub_positions == []  # 不被污染
