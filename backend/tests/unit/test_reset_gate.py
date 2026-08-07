# backend/tests/unit/test_reset_gate.py
"""根因1止血: reset_loss_protection_state 门控测试。

门控逻辑 _should_reset_loss_protection + _prev_loss_lock_status_map 已下沉到
paper_session_helpers.py(原位于 coordinator_loop.py),只包住
paper_auto_unlock_session 内部的 reset_loss_protection_state 一个调用,
其它 6 个副作用(策略恢复/解冻/重入冷却清除/MT-freeze 清除等)仍每 tick 执行。
"""
import pytest
from unittest.mock import MagicMock, patch

from backend.services.full_auto import paper_session_helpers as psh


@pytest.fixture(autouse=True)
def _clear_prev_status_map():
    """每个测试前后清空模块级 prev 状态 dict,避免测试间相互污染。"""
    psh._prev_loss_lock_status_map.clear()
    yield
    psh._prev_loss_lock_status_map.clear()


def test_reset_not_called_every_tick_when_state_not_transitioning():
    """心态状态机已设 leverage_cap<20 时,普通 tick 不应重置它。"""
    session = MagicMock()
    session.status = "running"  # 非 paused→running 转换
    # 首次调用(_prev=None):非转换 → 不 reset
    assert psh._should_reset_loss_protection(session) is False
    # 持续 running 仍不 reset
    assert psh._should_reset_loss_protection(session) is False


def test_reset_called_on_paused_to_running_transition():
    """session 从 paused→running 转换时应触发 reset。"""
    session = MagicMock()
    session.status = "paused"
    # 先记录 prev=paused
    psh._should_reset_loss_protection(session)
    # 现在转为 running
    session.status = "running"
    assert psh._should_reset_loss_protection(session) is True


def test_transition_detected_across_fresh_orm_instances():
    """生产关键场景:coordinator_loop 每个 tick 都新查出一个 ORM 对象,
    id() 每次都变。必须用稳定的 session_id 字符串做 key,转换检测才生效。
    若误用 id(session),此测试会失败(prev 永远 None → 转换永不触发)。
    """
    sid = "sess-abc-123"
    s_paused = MagicMock()
    s_paused.session_id = sid
    s_paused.status = "paused"
    # 第一次 tick:记录 prev=paused,非转换
    assert psh._should_reset_loss_protection(s_paused) is False

    # 下一个 tick:全新 ORM 对象实例,同一 session_id,status 变 running
    s_running = MagicMock()
    s_running.session_id = sid
    s_running.status = "running"
    # 应识别为 paused→running 转换
    assert psh._should_reset_loss_protection(s_running) is True

    # 后续 running tick(又是新实例):非转换,不再 reset
    s_running2 = MagicMock()
    s_running2.session_id = sid
    s_running2.status = "running"
    assert psh._should_reset_loss_protection(s_running2) is False


def test_only_reset_loss_protection_is_gated_other_side_effects_still_run():
    """issue#1 回归 + 2026-07-31：steady running tick 时
    reset_loss_protection_state 与 clear_state 都不应被调用。

    再入场冷却 clear_state 仅在 paused→running 转换时执行，
    否则会把止损冷却每 30s 抹掉（HYPE 17 秒同向再开根因）。
    MT-freeze 清除仍每 tick 执行。
    """
    db = MagicMock()
    session = MagicMock()
    session.session_id = "sess-gate-1"
    session.status = "running"  # steady running,非转换
    session.symbols = ["BTCUSDT", "ETHUSDT"]
    session.auto_coin_symbols = []
    session.active_strategy_ids = []
    session.terminated_strategy_ids = []
    # 预热 prev 状态:保证本次判定为非转换(不 reset)
    assert psh._should_reset_loss_protection(session) is False

    host = psh.PaperSessionHost(
        paper_loss_locks_disabled=lambda s: True,
        get_trading_account_id=lambda db, s: 42,
    )

    with patch(
        "backend.services.position_memory_manager.reset_loss_protection_state"
    ) as mock_reset, patch(
        "backend.services.reentry_cooldown.clear_state"
    ) as mock_clear_state:
        mock_reset.return_value = False
        psh.paper_auto_unlock_session(db, session, host)

        mock_reset.assert_not_called()
        mock_clear_state.assert_not_called()


def test_reset_loss_protection_called_on_transition_inside_unlock():
    """issue#1 回归保护: paused→running 转换时 reset_loss_protection_state
    在 paper_auto_unlock_session 内部应被调用。"""
    db = MagicMock()
    session = MagicMock()
    session.session_id = "sess-gate-2"
    session.status = "paused"
    # 记录 prev=paused
    psh._should_reset_loss_protection(session)
    # 转为 running(模拟 paper_auto_unlock_session 内部先把 status 设 running)
    session.status = "running"

    host = psh.PaperSessionHost(
        paper_loss_locks_disabled=lambda s: True,
        get_trading_account_id=lambda db, s: 42,
    )

    with patch(
        "backend.services.position_memory_manager.reset_loss_protection_state"
    ) as mock_reset:
        mock_reset.return_value = False
        psh.paper_auto_unlock_session(db, session, host)
        mock_reset.assert_called_once()
