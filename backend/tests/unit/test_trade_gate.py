# backend/tests/unit/test_trade_gate.py
import pytest
import threading
import time
from unittest.mock import MagicMock

from backend.services.leverage_authority import MIN_LEVERAGE


def _mock_db(existing_list):
    """构造 db mock:query().filter()x3.all() → existing_list(list)。

    TradeGate + PositionCoordinator 都消费 .all()(返回 list)。
    """
    db = MagicMock()
    chain = db.query.return_value.filter.return_value.filter.return_value.filter.return_value
    chain.all.return_value = existing_list
    chain.first.return_value = existing_list[0] if existing_list else None
    return db


def _pos(side, trade_nature=None, leverage=10, size=1.0):
    p = MagicMock()
    p.side = side
    p.trade_nature = trade_nature
    p.leverage = leverage
    p.size = size
    p.status = "open"
    return p


def test_gate_blocks_opposite_direction_same_symbol():
    """同 (account,symbol) 已有多仓时,空单应被闸拦截(向后兼容:tier/nature 未知)。
    注意:这里 side='sell' 是真实 order-side,DB 存 position-side 'long'。"""
    from backend.services.trade_gate import TradeGate
    db = _mock_db([_pos("long")])
    gate = TradeGate()
    # 不传 tier 也不传 trade_nature → 向后兼容:任意反向拦截
    decision = gate.check(db, account_id=1, symbol="BTC", side="sell",
                          leverage=10, tier=None)
    assert decision.allowed is False  # 方向冲突拦截


def test_gate_serializes_concurrent_opens():
    """两个线程同时对同 (account,symbol) acquire/release,应串行不交错。"""
    from backend.services.trade_gate import TradeGate
    gate = TradeGate()
    _order = []
    def _worker(name):
        gate.acquire(1, "BTC")
        _order.append(("start", name))
        time.sleep(0.05)
        _order.append(("end", name))
        gate.release(1, "BTC")
    t1 = threading.Thread(target=_worker, args=("A",))
    t2 = threading.Thread(target=_worker, args=("B",))
    t1.start(); t2.start(); t1.join(); t2.join()
    _seq = [n for _, n in _order]
    # 串行化:A 的 end 在 B 的 start 之前(或反之),无交错
    assert _seq in (["A", "A", "B", "B"], ["B", "B", "A", "A"]), f"got {_seq}"

def test_gate_applies_leverage_authority():
    """闸内用单一杠杆权威钳制(首仓:unified == requested → resolved 生效)。"""
    from backend.services.trade_gate import TradeGate
    gate = TradeGate()
    db = _mock_db([])  # 无既有仓
    d = gate.check(db, account_id=1, symbol="BTC", side="buy",
                   leverage=20, tier="long")
    assert d.allowed is True
    assert d.leverage == 12  # long tier cap

def test_gate_no_existing_position_allows():
    from backend.services.trade_gate import TradeGate
    gate = TradeGate()
    db = _mock_db([])
    d = gate.check(db, account_id=1, symbol="BTC", side="buy", leverage=10, tier="mid")
    assert d.allowed is True

def test_gate_allows_same_direction_add_with_real_vocab():
    """同向加仓:DB 存 long,新单 buy → 放行(用真实 buy/sell vs long/short 词汇)。"""
    from backend.services.trade_gate import TradeGate
    db = _mock_db([_pos("long", trade_nature="swing")])
    gate = TradeGate()
    # 调用方传 order-side buy
    d = gate.check(db, account_id=1, symbol="BTC", side="buy", leverage=10, tier="mid",
                   trade_nature="swing")
    assert d.allowed is True, f"同向加仓应放行: {d.reason}"


def test_gate_blocks_opposite_direction_with_real_vocab():
    """反向开仓(向后兼容,无 tier/nature):DB 存 long,新单 sell → 拦截。"""
    from backend.services.trade_gate import TradeGate
    db = _mock_db([_pos("long", trade_nature="swing")])
    gate = TradeGate()
    d = gate.check(db, account_id=1, symbol="BTC", side="sell", leverage=10, tier=None)
    assert d.allowed is False
    assert "direction_conflict" in d.reason


# ── tier-aware 新增用例 ────────────────────────────────────────────

def test_gate_same_tier_opposite_blocked():
    """同 trade_nature 反向 → 拦截(tier-aware;由 PositionCoordinator 拦截)。"""
    from backend.services.trade_gate import TradeGate
    db = _mock_db([_pos("long", trade_nature="scalp")])
    gate = TradeGate()
    d = gate.check(db, account_id=1, symbol="BTC", side="sell", leverage=10,
                   tier="short", trade_nature="scalp")
    assert d.allowed is False
    # 同 tier 冲突由协调器先拦截,reason 含 "conflict" 与具体 nature
    assert "conflict" in d.reason
    assert "scalp" in d.reason


def test_gate_cross_tier_opposite_allowed():
    """跨 tier 反向(scalp long + 新 trend short)→ 放行(合法对冲)。"""
    from backend.services.trade_gate import TradeGate
    db = _mock_db([_pos("long", trade_nature="scalp", leverage=10)])
    gate = TradeGate()
    d = gate.check(db, account_id=1, symbol="BTC", side="sell", leverage=12,
                   tier="long", trade_nature="trend_follow")
    assert d.allowed is True, f"跨 tier 对冲应放行: {d.reason}"


def test_gate_leverage_unified_with_existing():
    """现有 12x 持仓 + 新请求 long tier(cap 12)→ 杠杆钳制到 12(unified)。"""
    from backend.services.trade_gate import TradeGate
    db = _mock_db([_pos("long", trade_nature="trend_follow", leverage=12)])
    gate = TradeGate()
    d = gate.check(db, account_id=1, symbol="BTC", side="buy", leverage=20,
                   tier="long", trade_nature="trend_follow")
    assert d.allowed is True
    # long tier cap = 12; unified = max(12, 20) = 20; min(resolved=12, unified=20) = 12
    assert d.leverage == 12
