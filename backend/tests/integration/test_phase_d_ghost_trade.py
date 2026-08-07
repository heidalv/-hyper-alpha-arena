# backend/tests/integration/test_phase_d_ghost_trade.py
"""阶段D端到端:并发反向单经闸串行化,幽灵单消除。"""
import pytest
import threading
import time
from unittest.mock import MagicMock, patch

import sys
import os

# 确保仓库根在 path 上(便于 `import backend.services...`)
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def test_concurrent_opposite_orders_serialized_by_gate_lock():
    """两线程对同 (account,symbol) acquire/release,锁串行化,无交错。"""
    from backend.services.trade_gate import TradeGate
    gate = TradeGate()
    events = []
    def _worker(name):
        gate.acquire(1, "BTC")
        events.append(("acquired", name))
        time.sleep(0.03)
        events.append(("releasing", name))
        gate.release(1, "BTC")
    t1 = threading.Thread(target=_worker, args=("long_order",))
    t2 = threading.Thread(target=_worker, args=("short_order",))
    t1.start(); t2.start(); t1.join(); t2.join()
    # 验证串行:任一 worker 的 releasing 在另一 worker 的 acquired 之前
    names = [n for _, n in events]
    # 两个 worker 各自成对(acquired, releasing)且不交错
    assert names in (
        ["long_order", "long_order", "short_order", "short_order"],
        ["short_order", "short_order", "long_order", "long_order"],
    ), f"并发未串行化: {names}"


def test_ghost_trade_scenario_second_direction_blocked():
    """模拟幽灵单场景:已有 long 仓时,同 tier 的 short 单被方向冲突拒。"""
    from backend.services.trade_gate import TradeGate
    gate = TradeGate()
    db = MagicMock()
    _existing = MagicMock()
    _existing.side = "long"
    _existing.status = "open"
    _existing.trade_nature = "scalp"   # 同 tier(scalp)反向 → 拦截
    _existing.leverage = 10
    _existing.size = 1.0
    chain = db.query.return_value.filter.return_value.filter.return_value.filter.return_value
    chain.first.return_value = _existing
    chain.all.return_value = [_existing]   # coordinator + gate 都消费 .all()
    # 第二单 short(scalp)→ 闸拒(同 tier 反向)
    d = gate.check(db, account_id=1, symbol="BTC", side="short", leverage=10,
                   tier="short", trade_nature="scalp")
    assert d.allowed is False
    assert "conflict" in d.reason
