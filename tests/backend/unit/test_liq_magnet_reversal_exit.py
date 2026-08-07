"""清算磁吸反转硬退出单测（2026-07-07）。

背景：用户实盘反馈"大涨方向错误赔了不少"。排查发现已有 scalp/intraday 仓位
在遇到高强度反向清算磁吸信号时，此前系统只用该信号拦截"新开仓"，对"已持有
的反向仓位"完全不处理，只能被动等 master_running_reduce（历史胜率仅5%）
慢慢削减或等 SL 硬扛到底。

本测试覆盖 FullAutoTradingService._check_liq_magnet_reversal_exit 的核心分支：
1. 无反向仓位 → 不平仓
2. 有反向仓位但磁吸不是 high severity / 方向不匹配 → 不平仓
3. 有反向仓位 + high severity 反向磁吸 → 平仓，reason=liq_magnet_reversal
4. master_close_guard 的硬事实白名单已包含 liq_magnet_reversal
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def _make_position(side="short", strategy_id="scalp_lane_abc123"):
    return SimpleNamespace(side=side, strategy_id=strategy_id, status="open")


def _run(db, account_id="1", symbol="BTC", router_direction="long"):
    from backend.services.full_auto_trading_service import FullAutoTradingService as F
    # 该方法不读写任何 self 状态，直接以 None 作 self 调用（与本文件同目录下
    # test_remediation_regression_2026_07_06.py 里 F._decision_price_consistency_ok
    # 的调用方式一致）。
    return F._check_liq_magnet_reversal_exit(
        None, db=db, account_id=account_id, symbol=symbol,
        router_direction=router_direction,
    )


def test_no_opposite_position_skips_close():
    """symbol 没有反向仓位 → 不查磁吸、不平仓。"""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    with patch("backend.services.crypto_alpha_signals.crypto_alpha") as _ca, \
         patch("backend.services.paper_trading_engine.paper_engine") as _pe:
        _run(db)
        _ca.liquidation_magnet.assert_not_called()
        _pe.close_position.assert_not_called()


def test_opposite_position_but_low_severity_skips_close():
    """有反向仓位，但磁吸 severity 不是 high → 不平仓。"""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _make_position()

    lm = SimpleNamespace(available=True, severity="medium", direction="long", note="弱磁吸")
    with patch("backend.services.crypto_alpha_signals.crypto_alpha") as _ca, \
         patch("backend.services.paper_trading_engine.paper_engine") as _pe:
        _ca.liquidation_magnet.return_value = lm
        _run(db, router_direction="long")
        _pe.close_position.assert_not_called()


def test_opposite_position_but_direction_mismatch_skips_close():
    """磁吸是 high severity，但方向与 router_direction 不一致 → 不平仓。"""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _make_position(side="short")

    lm = SimpleNamespace(available=True, severity="high", direction="short", note="下方磁吸")
    with patch("backend.services.crypto_alpha_signals.crypto_alpha") as _ca, \
         patch("backend.services.paper_trading_engine.paper_engine") as _pe:
        _ca.liquidation_magnet.return_value = lm
        # router_direction=long 但磁吸方向是 short → 不匹配，不应平仓
        _run(db, router_direction="long")
        _pe.close_position.assert_not_called()


def test_high_severity_reversal_triggers_close():
    """持有空单，出现 high severity 向上磁吸(=long) → 平掉空单。"""
    db = MagicMock()
    pos = _make_position(side="short", strategy_id="scalp_lane_de1234")
    db.query.return_value.filter.return_value.first.return_value = pos

    lm = SimpleNamespace(
        available=True, severity="high", direction="long",
        note="上方磁吸(severity=high,清算$2.1M)",
    )
    with patch("backend.services.crypto_alpha_signals.crypto_alpha") as _ca, \
         patch("backend.services.paper_trading_engine.paper_engine") as _pe:
        _ca.liquidation_magnet.return_value = lm
        _pe.close_position.return_value = {"pnl": -0.9}

        _run(db, account_id="14", symbol="BTC", router_direction="long")

        _pe.close_position.assert_called_once()
        args, kwargs = _pe.close_position.call_args
        assert args[0] is db
        assert args[1] == "14"
        assert args[2] == "BTC"
        assert args[3] == "short"  # 平的是反向的空单
        assert kwargs["reason"] == "liq_magnet_reversal"
        assert kwargs["strategy_id"] == "scalp_lane_de1234"
        db.commit.assert_called_once()


def test_close_returns_none_does_not_raise():
    """paper_engine 返回 None（比如仓位已被别的路径抢先平掉）也不应报错。"""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = _make_position()

    lm = SimpleNamespace(available=True, severity="high", direction="short", note="下方磁吸")
    with patch("backend.services.crypto_alpha_signals.crypto_alpha") as _ca, \
         patch("backend.services.paper_trading_engine.paper_engine") as _pe:
        _ca.liquidation_magnet.return_value = lm
        _pe.close_position.return_value = None
        _run(db, router_direction="short")
        _pe.close_position.assert_called_once()


def test_master_close_guard_whitelists_liq_magnet_reversal():
    """master_close_guard 的硬事实白名单必须包含 liq_magnet_reversal，
    否则 unified_exit_executor / _execute_master_decisions 等下游复核路径
    可能会把这次主动退出误判为"无硬事实"而拦截。"""
    from backend.services.master_close_guard import check_master_close_hardfact

    result = check_master_close_hardfact(
        tier="short", action="close",
        entry_price=62024.0, mark_price=63000.0, sl_price=61000.0,
        unrealized_pnl=-5.0, margin=50.0,
        reason_hint="liq_magnet_reversal: 上方磁吸(severity=high)",
    )
    assert result.allow is True
    assert result.matched_rule == "hard_reason:liq_magnet_reversal"
