"""SymbolLockRegistry 单元测试"""
import time
import pytest


pytestmark = pytest.mark.unit

# 2026-06-19: 测试默认设为 live 模式（不跳过锁仓），paper 模式测试单独 mock。
# 用模块级 flag 替代 patch（lock_strength_service 在方法内部 import，patch 路径复杂）
import backend.services.symbol_lock_registry as _slr_mod

@pytest.fixture(autouse=True)
def force_live_mode():
    """每个测试默认强制 live 模式（_FORCE_LIVE=True 跳过 paper 检查）。"""
    _slr_mod._FORCE_LIVE_FOR_TESTS = True
    yield
    _slr_mod._FORCE_LIVE_FOR_TESTS = False


def test_lock_and_is_locked():
    """锁定后 is_locked 返回 True。"""
    from backend.services.symbol_lock_registry import SymbolLockRegistry
    reg = SymbolLockRegistry()
    reg._locks.clear()
    reg._hysteresis_counts.clear()
    reg.lock("BTC", reason_code="per_symbol_loss", by="test", duration_sec=60)
    assert reg.is_locked("BTC") is True


def test_unlock():
    """解锁后 is_locked 返回 False。"""
    from backend.services.symbol_lock_registry import SymbolLockRegistry
    reg = SymbolLockRegistry()
    reg._locks.clear()
    reg._hysteresis_counts.clear()
    reg.lock("BTC", reason_code="per_symbol_loss", by="test", duration_sec=60)
    assert reg.is_locked("BTC") is True
    reg.unlock("BTC", reason_code="per_symbol_loss")
    assert reg.is_locked("BTC") is False


def test_not_locked_returns_false():
    """未锁定的 symbol 返回 False。"""
    from backend.services.symbol_lock_registry import SymbolLockRegistry
    reg = SymbolLockRegistry()
    reg._locks.clear()
    reg._hysteresis_counts.clear()
    assert reg.is_locked("ETH") is False


def test_symbol_level_lock_blocks_all_strategies():
    """symbol 级锁（strategy_id=None）阻止所有策略。"""
    from backend.services.symbol_lock_registry import SymbolLockRegistry
    reg = SymbolLockRegistry()
    reg._locks.clear()
    reg._hysteresis_counts.clear()
    reg.lock("BTC", strategy_id=None, reason_code="orchestrator_frozen", by="test", duration_sec=60)
    assert reg.is_locked("BTC") is True
    assert reg.is_locked("BTC", strategy_id="strat_1") is True
    assert reg.is_locked("BTC", strategy_id="strat_2") is True


def test_strategy_level_lock_only_blocks_that_strategy():
    """策略级锁只阻止该策略。"""
    from backend.services.symbol_lock_registry import SymbolLockRegistry
    reg = SymbolLockRegistry()
    reg._locks.clear()
    reg._hysteresis_counts.clear()
    reg.lock("BTC", strategy_id="strat_1", reason_code="health_pause", by="test", duration_sec=60)
    assert reg.is_locked("BTC", strategy_id="strat_1") is True
    assert reg.is_locked("BTC", strategy_id="strat_2") is False


def test_hysteresis_increases_duration():
    """重复锁定同一 symbol+reason，持续时间递增。"""
    from backend.services.symbol_lock_registry import SymbolLockRegistry
    reg = SymbolLockRegistry()
    reg._locks.clear()
    reg._hysteresis_counts.clear()
    # 第一次锁（base=60s）
    reg.lock("BTC", reason_code="per_symbol_loss", by="test", duration_sec=60)
    r1 = list(reg._locks.values())[0]
    d1 = r1.expires_at - r1.locked_at
    reg.unlock("BTC", reason_code="per_symbol_loss")

    # 第二次锁（应该 ×2 = 120s）
    reg.lock("BTC", reason_code="per_symbol_loss", by="test", duration_sec=60)
    r2 = list(reg._locks.values())[0]
    d2 = r2.expires_at - r2.locked_at
    assert d2 > d1, f"第二次锁定应更长: {d2} > {d1}"


def test_expired_lock_not_active():
    """过期锁不再 active。"""
    from backend.services.symbol_lock_registry import SymbolLockRegistry
    reg = SymbolLockRegistry()
    reg._locks.clear()
    reg._hysteresis_counts.clear()
    # 锁 1 秒
    reg.lock("BTC", reason_code="ranging", by="test", duration_sec=1)
    assert reg.is_locked("BTC") is True
    time.sleep(1.1)
    assert reg.is_locked("BTC") is False


def test_cleanup_expired():
    """cleanup_expired 清理过期锁。"""
    from backend.services.symbol_lock_registry import SymbolLockRegistry
    reg = SymbolLockRegistry()
    reg._locks.clear()
    reg._hysteresis_counts.clear()
    reg.lock("BTC", reason_code="ranging", by="test", duration_sec=1)
    reg.lock("ETH", reason_code="per_symbol_loss", by="test", duration_sec=300)
    time.sleep(1.1)
    removed = reg.cleanup_expired()
    assert removed == 1  # BTC 过期了，ETH 没过期
    assert reg.is_locked("BTC") is False
    assert reg.is_locked("ETH") is True


def test_should_skip_revive_locked_strategy():
    """被锁的策略 should_skip_revive=True。"""
    from backend.services.symbol_lock_registry import SymbolLockRegistry
    reg = SymbolLockRegistry()
    reg._locks.clear()
    reg._hysteresis_counts.clear()
    reg.lock("BTC", reason_code="per_symbol_loss", by="test", duration_sec=300)
    assert reg.should_skip_revive("BTC", "strat_1") is True


def test_should_skip_revive_expired_allows_revive():
    """过期锁的策略 should_skip_revive=False（可恢复）。"""
    from backend.services.symbol_lock_registry import SymbolLockRegistry
    reg = SymbolLockRegistry()
    reg._locks.clear()
    reg._hysteresis_counts.clear()
    reg.lock("BTC", reason_code="per_symbol_loss", by="test", duration_sec=1)
    time.sleep(1.1)
    assert reg.should_skip_revive("BTC", "strat_1") is False


def test_should_skip_revive_manual_never_auto_revive():
    """manual 锁即使过期也不自动恢复。"""
    from backend.services.symbol_lock_registry import SymbolLockRegistry
    reg = SymbolLockRegistry()
    reg._locks.clear()
    reg._hysteresis_counts.clear()
    reg.lock("BTC", reason_code="manual", by="user", duration_sec=1)
    time.sleep(1.1)
    # manual 需显式 unlock
    assert reg.should_skip_revive("BTC", "strat_1") is True
    reg.unlock("BTC", reason_code="manual")
    assert reg.should_skip_revive("BTC", "strat_1") is False


def test_get_lock_reason():
    """获取锁定原因。"""
    from backend.services.symbol_lock_registry import SymbolLockRegistry
    reg = SymbolLockRegistry()
    reg._locks.clear()
    reg._hysteresis_counts.clear()
    reg.lock("BTC", reason_code="crash", by="test", duration_sec=-1)
    assert reg.get_lock_reason("BTC") == "crash"
    assert reg.get_lock_reason("ETH") is None


def test_unlock_all():
    """unlock_all 解除某 symbol 所有锁。"""
    from backend.services.symbol_lock_registry import SymbolLockRegistry
    reg = SymbolLockRegistry()
    reg._locks.clear()
    reg._hysteresis_counts.clear()
    reg.lock("BTC", reason_code="per_symbol_loss", by="test", duration_sec=300)
    reg.lock("BTC", strategy_id="s1", reason_code="health_pause", by="test", duration_sec=300)
    count = reg.unlock_all("BTC")
    assert count >= 1
    assert reg.is_locked("BTC") is False


def test_paper_mode_skips_non_system_locks():
    """Paper 模式跳过非系统级锁仓（per_symbol_loss/consec_loss/ranging 等）。"""
    import backend.services.symbol_lock_registry as _slr_mod
    from unittest.mock import MagicMock
    _slr_mod._FORCE_LIVE_FOR_TESTS = False
    reg = _slr_mod.SymbolLockRegistry()
    reg._locks.clear()
    reg._hysteresis_counts.clear()

    # Mock lock_strength_service 返回 paper profile
    _mock_svc = MagicMock()
    _mock_svc.get_profile.return_value.disable_loss_locks = True
    import sys
    sys.modules['backend.services.lock_strength_service'] = _mock_svc

    try:
        # per_symbol_loss 应被跳过
        result = reg.lock("BTC", reason_code="per_symbol_loss", by="test")
        assert result is False, "paper 模式应跳过 per_symbol_loss"
        assert reg.is_locked("BTC") is False

        # manual 应允许
        result = reg.lock("SOL", reason_code="manual", by="user")
        assert result is True
        assert reg.is_locked("SOL") is True

        # deadlock 应允许
        result = reg.lock("XPL", reason_code="deadlock", by="system")
        assert result is True
    finally:
        _slr_mod._FORCE_LIVE_FOR_TESTS = True
        # 恢复
        if 'backend.services.lock_strength_service' in sys.modules:
            del sys.modules['backend.services.lock_strength_service']


def test_live_mode_allows_all_locks():
    """Live 模式允许所有锁仓。"""
    import backend.services.symbol_lock_registry as _slr_mod
    _slr_mod._FORCE_LIVE_FOR_TESTS = True  # live 模式
    reg = _slr_mod.SymbolLockRegistry()
    reg._locks.clear()
    reg._hysteresis_counts.clear()

    result = reg.lock("BTC", reason_code="per_symbol_loss", by="test", duration_sec=60)
    assert result is True, "live 模式应允许 per_symbol_loss"
    assert reg.is_locked("BTC") is True
