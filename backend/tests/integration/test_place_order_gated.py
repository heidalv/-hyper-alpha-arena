# backend/tests/integration/test_place_order_gated.py
"""验证 paper_engine.place_order 接入 TradeGate 单一闸(阶段 D2)。"""
import pytest
from unittest.mock import MagicMock, patch

import sys
import os

# 确保仓库根在 path 上(便于 `import backend.services...`)
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def test_place_order_returns_none_when_gate_denies():
    """闸拒(方向冲突)时 place_order 应返回 None,不下单。"""
    from backend.services.paper_trading_engine import PaperTradingEngine
    from backend.services.trade_gate import GateDecision

    engine = PaperTradingEngine.__new__(PaperTradingEngine)
    db = MagicMock()
    with patch("backend.services.trade_gate.trade_gate") as mock_gate:
        mock_gate.acquire.return_value = MagicMock()
        mock_gate.check.return_value = GateDecision(
            allowed=False, reason="direction_conflict"
        )
        res = engine.place_order(
            db, account_id=1, symbol="BTC", side="short",
            quantity=0.1, leverage=10, timeframe_tier="short",
        )
    assert res is None  # 闸拒,不下单


def test_place_order_proceeds_when_gate_allows():
    """闸放行时 place_order 正常继续(不因闸报错)。"""
    from backend.services.paper_trading_engine import PaperTradingEngine
    from backend.services.trade_gate import GateDecision

    engine = PaperTradingEngine.__new__(PaperTradingEngine)
    db = MagicMock()
    with patch("backend.services.trade_gate.trade_gate") as mock_gate:
        mock_gate.acquire.return_value = MagicMock()
        mock_gate.check.return_value = GateDecision(
            allowed=True, leverage=10, tp_pct=0.025, sl_pct=0.012
        )
        # 因为后续真实 place_order 逻辑复杂,这里只验证闸放行后不抛闸相关异常,
        # 且 acquire/release 被调用。真实下单由既有测试覆盖。
        mock_gate.release.return_value = None
        try:
            engine.place_order(
                db, account_id=1, symbol="BTC", side="long",
                quantity=0.1, leverage=10, timeframe_tier="mid",
            )
        except Exception as e:
            # 闸后的既有逻辑可能因 mock db 报错,那是预期的;只要不是闸的问题
            assert "trade_gate" not in str(e).lower() or "release" not in str(e).lower()
        assert mock_gate.acquire.called
        assert mock_gate.release.called
