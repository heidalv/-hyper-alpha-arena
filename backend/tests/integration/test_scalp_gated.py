# backend/tests/integration/test_scalp_gated.py
"""验证 scalp 下单经 place_order 自动过 TradeGate(不再绕过)。"""
import pytest
from unittest.mock import MagicMock, patch

import sys
import os

# 确保仓库根在 path 上(便于 `import backend.services...`)
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def test_scalp_place_order_invokes_gate():
    """scalp 调 paper_engine.place_order 时,TradeGate.acquire/check 被调用。"""
    from backend.services.paper_trading_engine import PaperTradingEngine
    from backend.services.trade_gate import GateDecision
    engine = PaperTradingEngine.__new__(PaperTradingEngine)
    db = MagicMock()
    with patch("backend.services.trade_gate.trade_gate") as mock_gate:
        mock_gate.acquire.return_value = MagicMock()
        mock_gate.check.return_value = GateDecision(
            allowed=True, leverage=20, tp_pct=0.025, sl_pct=0.012)
        # scalp 调 place_order(scalp_loop 也是这么调的)
        try:
            engine.place_order(db, account_id=1, symbol="BTC", side="long",
                               quantity=0.01, leverage=20, trade_nature="scalp",
                               timeframe_tier="short")
        except Exception:
            pass  # 后续逻辑因 mock db 报错是预期;只验证闸被调
        assert mock_gate.acquire.called, "scalp 下单必须经闸 acquire"
        assert mock_gate.check.called, "scalp 下单必须经闸 check"
        assert mock_gate.release.called, "scalp 下单后必须释放锁"


def test_scalp_direction_conflict_blocked_by_gate():
    """scalp 反向单被闸拦截(方向冲突 → place_order 返回 None)。"""
    from backend.services.paper_trading_engine import PaperTradingEngine
    from backend.services.trade_gate import GateDecision
    engine = PaperTradingEngine.__new__(PaperTradingEngine)
    db = MagicMock()
    with patch("backend.services.trade_gate.trade_gate") as mock_gate:
        mock_gate.acquire.return_value = MagicMock()
        mock_gate.check.return_value = GateDecision(
            allowed=False, reason="direction_conflict")
        res = engine.place_order(db, account_id=1, symbol="BTC", side="short",
                                 quantity=0.01, leverage=20, trade_nature="scalp",
                                 timeframe_tier="short")
    assert res is None  # 闸拒,scalp 反向单不下
