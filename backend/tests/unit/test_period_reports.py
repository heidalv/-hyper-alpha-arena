# -*- coding: utf-8 -*-
"""M3: 三周期报告观测体系——亏损归因 + 日报结构。"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.loss_attribution import build_loss_attribution, _tier_of_trade


def _mk_pos(symbol="BTC", pnl=-10.0, reason="chandelier", nature="trend_follow", tf="long"):
    """D3 口径：PaperPosition 风格 mock，pnl 经 entry/close 价差复原。"""
    p = MagicMock()
    p.symbol = symbol
    p.side = "long"
    p.partial_realized_pnl = 0.0
    p.close_reason = reason
    p.entry_price = 100.0
    p.size = 1.0
    p.close_price = 100.0 + pnl  # long: realized = (close-entry)*size
    p.trade_nature = nature
    p.timeframe_tier = tf
    return p


def _mk_db(rows):
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.all.return_value = rows
    db.query.return_value = q
    return db


def test_tier_of_trade():
    assert _tier_of_trade("trend_follow", None) == "long"
    assert _tier_of_trade("swing", None) == "midlong"
    assert _tier_of_trade("intraday", "short") == "scalp"


def test_loss_attribution_triggers_on_loss():
    db = _mk_db([_mk_pos("BTC", -20.0, "chandelier"),
                 _mk_pos("ETH", -5.0, "structure_break"),
                 _mk_pos("BTC", -8.0, "chandelier")])
    out = build_loss_attribution(db, 1, "long", days=1)
    assert out["active"] is True
    assert out["total_pnl"] == -33.0
    assert len(out["by_symbol"]) == 2  # BTC(-28) ETH(-5)
    assert out["by_symbol"][0]["key"] == "BTC"  # 亏损最重在前


def test_loss_attribution_inactive_on_profit():
    db = _mk_db([_mk_pos("BTC", 20.0, "chandelier")])
    out = build_loss_attribution(db, 1, "long", days=1)
    assert out["active"] is False and "盈利" in out["note"]


def test_loss_attribution_inactive_no_samples():
    db = _mk_db([])
    out = build_loss_attribution(db, 1, "scalp", days=1)
    assert out["active"] is False and "无平仓样本" in out["note"]


def test_daily_report_builds_three_sections():
    """build_daily_report 产出三周期段且长线段含 L1 面板。"""
    from backend.services.period_daily_report import build_daily_report
    db = MagicMock()
    # 交易统计查询返回空
    q = MagicMock()
    q.filter.return_value = q
    q.all.return_value = []
    db.query.return_value = q
    with patch("backend.services.period_daily_report._l1_panel", return_value={"BTC": {"state": "up"}}), \
         patch("backend.services.period_daily_report._open_positions", return_value=[]), \
         patch("backend.services.loss_attribution.build_loss_attribution", return_value={"active": False, "note": "无样本"}):
        rep = build_daily_report(db, 1)
    assert set(rep["sections"].keys()) == {"scalp", "midlong", "long"}
    assert rep["sections"]["long"]["l1_panel"]["BTC"]["state"] == "up"
    assert "loss_attribution" in rep["sections"]["midlong"]
