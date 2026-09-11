# -*- coding: utf-8 -*-
"""CapitalAllocationCoordinator 重入死锁回归（2026-09-02）。

Bug：self._lock 是 threading.Lock（不可重入），而

    get_strategy_sub_available()      -> with self._lock
        _get_strategy_sub_available()
            get_rebate_available()    -> with self._lock   # 自己等自己

凡 strategy_id 未配置子池（或 pct<=0）就走这条分支，100% 永久挂起。
另一个调用点 request_capital()（同样在锁内调 _get_strategy_sub_available）
受同一 bug 影响，即返利套利的实际下单路径也会挂死 —— 不只是测试。

本用例用带超时的子线程断言"能返回"，避免回归时把整个测试进程拖死。
"""
import threading
import pytest


@pytest.fixture
def coord():
    from backend.services.rebate_arb.capital_coordinator import CapitalAllocationCoordinator
    c = CapitalAllocationCoordinator()
    c.initialize(10000.0, force_reset=True)
    return c


def _call_with_timeout(fn, timeout=5.0):
    """在子线程里调用，超时即判定死锁。返回 (完成?, 结果, 异常)。"""
    box = {}

    def _run():
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 - 死锁排查需要看到任何异常
            box["error"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    return (not t.is_alive()), box.get("value"), box.get("error")


class TestNoReentrantDeadlock:
    def test_unknown_strategy_id_returns(self, coord):
        """未配置子池的 strategy_id —— 这正是触发死锁的输入。"""
        done, val, err = _call_with_timeout(
            lambda: coord.get_strategy_sub_available("NOT_A_POOL")
        )
        assert done, "get_strategy_sub_available 未在 5s 内返回：重入死锁复现"
        assert err is None, f"不应抛异常: {err!r}"
        assert isinstance(val, float)

    @pytest.mark.parametrize("sid", ["", None, "s1", "UNKNOWN_99"])
    def test_various_ids_return(self, coord, sid):
        done, _, err = _call_with_timeout(
            lambda: coord.get_strategy_sub_available(sid)
        )
        assert done, f"strategy_id={sid!r} 挂死"
        assert err is None, f"strategy_id={sid!r} 抛异常: {err!r}"

    def test_fallback_equals_rebate_available(self, coord):
        """回退分支的语义必须与 get_rebate_available() 完全一致。"""
        done, val, err = _call_with_timeout(
            lambda: coord.get_strategy_sub_available("NOT_A_POOL")
        )
        assert done and err is None
        assert val == pytest.approx(coord.get_rebate_available())

    def test_request_capital_path_returns(self, coord):
        """request_capital 也在锁内调 _get_strategy_sub_available（第二个受害点）。"""
        done, _, err = _call_with_timeout(
            lambda: coord.request_capital(
                "rebate_points_arb", 100.0, strategy_id="NOT_A_POOL"
            ),
            timeout=8.0,
        )
        assert done, "request_capital 挂死：重入死锁未修净"
        assert err is None or not isinstance(err, TypeError), f"签名不符: {err!r}"

    def test_repeated_calls_do_not_leak_lock(self, coord):
        """连续调用不得逐次泄漏锁（第一次成功、第二次挂死是典型症状）。"""
        for i in range(5):
            done, _, err = _call_with_timeout(
                lambda: coord.get_strategy_sub_available("NOT_A_POOL"), timeout=3.0
            )
            assert done, f"第 {i + 1} 次调用挂死"
            assert err is None


class TestPrivateHelperContract:
    def test_helper_does_not_take_lock_itself(self, coord):
        """_get_strategy_sub_available 约定"调用者已持锁"，自身不得再取锁。"""
        with coord._lock:  # noqa: SLF001 - 正是要验证这个契约
            done, _, err = _call_with_timeout(
                lambda: coord._get_strategy_sub_available("NOT_A_POOL"),  # noqa: SLF001
                timeout=3.0,
            )
        assert done, "持锁状态下调用私有 helper 挂死：它内部仍在取锁"
        assert err is None

    def test_source_has_no_public_getter_call(self):
        """源码层面钉死：helper 的**可执行代码**里不得再调取锁的公开方法。

        注释中提及旧写法是允许的（本次修复就在注释里留了病因），故先剥注释。
        """
        import inspect
        from backend.services.rebate_arb.capital_coordinator import CapitalAllocationCoordinator

        src = inspect.getsource(
            CapitalAllocationCoordinator._get_strategy_sub_available
        )
        code_only = "\n".join(
            line.split("#", 1)[0] for line in src.splitlines()
        )
        for banned in ("self.get_rebate_available(", "self.get_status(",
                       "self.get_all_utilization(", "self.get_strategy_sub_available("):
            assert banned not in code_only, (
                f"_get_strategy_sub_available 调用了取锁的公开方法 {banned}…)，"
                f"与调用方的 self._lock 构成重入死锁"
            )
