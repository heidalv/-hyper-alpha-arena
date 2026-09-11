"""mid 开仓路径必须注入 indicators，否则 StrictData 会拦死 LLM 主脑。"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_try_execute_injects_mid_indicators(monkeypatch):
    from backend.services.full_auto import midlong_helpers as mh

    calls = []

    def _fake_inject(ms, symbol, include_weekly=False):
        calls.append((symbol, include_weekly))
        block = ms.setdefault(symbol, {})
        block["price"] = 100.0
        block["indicators_1h"] = {"trend": "up", "macd": 1, "adx": 20}
        block["indicators_4h"] = {"trend": "up", "macd": 1, "adx": 20}
        block["indicators_1d"] = {"trend": "up", "macd": 1, "adx": 20}

    monkeypatch.setattr(mh, "inject_midlong_indicators", _fake_inject)

    host = MagicMock()
    host.append_event = MagicMock()
    host.evaluate_and_execute_proposal = MagicMock(return_value=False)
    host.get_trading_account_id = MagicMock(return_value=14)

    db = MagicMock()
    session = MagicMock()
    session.session_id = "fa_test"
    session.trading_mode = "paper"
    session.paper_account_id = 14

    market = {"BTC": {"current_price": 100.0}}
    # 走到注入后会被后续闸拦住也没关系；断言注入已被调用且 market 已填指标
    mh.try_execute_independent_agent_open(
        db=db,
        session=session,
        sym="BTC",
        tier="mid",
        action="buy",
        confidence=60,
        sl_pct=0.05,
        tp_pct=0.09,
        trade_nature="swing",
        market_summary=market,
        session_mode="running",
        host=host,
    )
    assert calls and calls[0][0] == "BTC" and calls[0][1] is False
    assert "indicators_1h" in market["BTC"]
    assert market["BTC"].get("price") == 100.0
