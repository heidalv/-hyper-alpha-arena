"""PortfolioBudget 冻结路径死锁回归测试（2026-09-02）。

原病症：``_freeze`` 在 ``PB_FREEZE_ENABLED=false`` 分支里**持锁**调用
``_spawn_repair``，而后者开头也要 ``with self._lock``。``self._lock`` 是普通
``threading.Lock``（不可重入），同一线程二次申请必然自锁死。

完整链路：``evaluate_open → _freeze_via_coordinator → freeze_coordinator.freeze
→ _freeze → _spawn_repair``。死锁后这把全局锁永不释放，PortfolioBudget 的开仓
评估会整体卡住 —— 而生产 ``.env`` 正是 ``PB_FREEZE_ENABLED=false``，这条路径是
活跃的（全量回归里 ``test_daily_var_block`` 永久挂起就是撞上了它）。

这类缺陷不会以"报错"的形式暴露，只会表现为"卡住不动"，所以必须有显式用例守住。
"""
from __future__ import annotations

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from backend.services.risk_management import portfolio_budget as pb


@pytest.fixture
def budget(monkeypatch):
    """全新实例 + 记录 _spawn_repair 调用时的持锁状态。"""
    b = pb.PortfolioBudget()
    calls = []

    def _spy(account_id, strategy, symbol, why, scope):
        # 非阻塞取锁：拿得到 → 说明调用发生在锁外（正确）
        got = b._lock.acquire(blocking=False)
        if got:
            b._lock.release()
        calls.append({"args": (account_id, strategy, symbol, why, scope),
                      "lock_free": got})

    monkeypatch.setattr(b, "_spawn_repair", _spy)
    return b, calls


def _run_with_timeout(fn, seconds=5.0):
    """在子线程里跑，超时即判定死锁（不让整个套件挂住）。"""
    done = threading.Event()
    err = []

    def _target():
        try:
            fn()
        except BaseException as e:  # noqa: BLE001 - 需要透传给主线程断言
            err.append(e)
        finally:
            done.set()

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    finished = done.wait(seconds)
    return finished, (err[0] if err else None)


@pytest.mark.timeout(30)
def test_freeze_disabled_path_does_not_deadlock(budget, monkeypatch):
    """PB_FREEZE_ENABLED=false（生产实况）时冻结路径必须能返回。"""
    b, calls = budget
    monkeypatch.setenv("PB_FREEZE_ENABLED", "false")

    finished, err = _run_with_timeout(
        lambda: b._freeze(1, "scalp", "BTC", "daily_var", scope="key"))

    assert finished, "冻结路径未在超时内返回 —— 自锁死复发"
    assert err is None, f"冻结路径抛异常: {err!r}"
    assert len(calls) == 1, "禁用冻结时仍应启动修复流水线"
    assert calls[0]["lock_free"], "_spawn_repair 在持锁状态下被调用 —— 会自锁死"


@pytest.mark.timeout(30)
def test_freeze_enabled_path_still_spawns_repair_outside_lock(budget, monkeypatch):
    """正常冻结路径同样要在锁外触发修复（保持两条路径同构）。"""
    b, calls = budget
    monkeypatch.setenv("PB_FREEZE_ENABLED", "true")

    finished, err = _run_with_timeout(
        lambda: b._freeze(1, "scalp", "ETH", "daily_var", scope="key"))

    assert finished, "冻结路径未在超时内返回"
    assert err is None, f"冻结路径抛异常: {err!r}"
    assert len(calls) == 1
    assert calls[0]["lock_free"], "_spawn_repair 在持锁状态下被调用"
    # 冻结时间戳确实写入
    assert b._key_frozen_until.get((1, "scalp", "ETH"), 0.0) > 0


@pytest.mark.timeout(30)
def test_lock_released_after_freeze(budget, monkeypatch):
    """_freeze 返回后锁必须是空闲的（否则后续开仓评估全卡住）。"""
    b, _ = budget
    for flag in ("false", "true"):
        monkeypatch.setenv("PB_FREEZE_ENABLED", flag)
        finished, _err = _run_with_timeout(
            lambda: b._freeze(1, "trend", "SOL", "test", scope="key"))
        assert finished
        got = b._lock.acquire(blocking=False)
        assert got, f"PB_FREEZE_ENABLED={flag} 返回后锁仍被持有"
        b._lock.release()


@pytest.mark.timeout(30)
def test_repeat_trigger_within_freeze_window_returns_early(budget, monkeypatch):
    """冻结期内重复触发直接返回，不重复启动修复流水线。"""
    b, calls = budget
    monkeypatch.setenv("PB_FREEZE_ENABLED", "true")

    finished, _ = _run_with_timeout(
        lambda: b._freeze(1, "scalp", "XRP", "first", scope="key"))
    assert finished and len(calls) == 1

    finished2, _ = _run_with_timeout(
        lambda: b._freeze(1, "scalp", "XRP", "second", scope="key"))
    assert finished2, "重复触发路径未返回"
    assert len(calls) == 1, "冻结期内重复触发不应再次启动修复流水线"


def test_no_spawn_repair_inside_lock_block():
    """源码契约：_freeze 的锁块内不得出现 _spawn_repair 调用。"""
    import inspect

    src = inspect.getsource(pb.PortfolioBudget._freeze)
    lines = src.splitlines()
    lock_indent = None
    offenders = []
    for ln in lines:
        stripped = ln.strip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("with self._lock"):
            lock_indent = len(ln) - len(ln.lstrip())
            continue
        if lock_indent is None:
            continue
        indent = len(ln) - len(ln.lstrip())
        if stripped and indent <= lock_indent:
            lock_indent = None  # 离开锁块
            continue
        if "_spawn_repair" in stripped:
            offenders.append(stripped)

    assert not offenders, (
        "锁块内调用 _spawn_repair 会自锁死（该函数自己也要取同一把锁）:\n  "
        + "\n  ".join(offenders)
    )
