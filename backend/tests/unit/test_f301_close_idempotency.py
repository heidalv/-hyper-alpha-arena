# -*- coding: utf-8 -*-
"""[F301 2026-09-16] 双平仓守卫的契约测试。

现场事故（本会话真实发生）：人工按"换宇宙操作性平仓"平掉 ADA 并写了平仓腿，
但**运行中的进程持有内存态**，下一次 `save_states` 把 `lane_runtime_state`
覆写回 qty=154.02。随后 worker 重启，旧的 `load_states` 无条件相信状态文件
⇒ 装回一个已经不存在的持仓 ⇒ 5 秒后超过 1 小时超时 ⇒ **车道又平了一次** ⇒
账本净额变成 −154.02（凭空多出一个空头），前端显示 −$29.98。

本测试锁定修复后的四条不变量：
  1. `load_states` 必须与 `lane_ledger` 对账（账本是事实源）；
  2. 分叉时**采用账本数量**，并清掉 `opened_ts`（否则超时/止损会再次触发）；
  3. 状态与账本一致时**不得**误报（无假阳性）；
  4. `reload_states()` 提供运行期对齐（不必重启进程），且端点已接线。
"""
from __future__ import annotations

import inspect

import pytest


def test_load_states_calls_reconciliation():
    """load_states 必须调用 _reconcile_loaded_states（而不是直接信任状态文件）。"""
    from backend.services.market_maker import runner as R

    src = inspect.getsource(R.ShadowRunner.load_states)
    assert "_reconcile_loaded_states" in src, (
        "load_states 必须与账本对账 —— 否则陈旧运行态会被装回并触发重复平仓")


def test_reconcile_uses_ledger_as_source_of_truth():
    from backend.services.market_maker import runner as R

    src = inspect.getsource(R.ShadowRunner._reconcile_loaded_states)
    assert "ledger_positions" in src or "open_positions" in src
    # 清掉开仓时刻是防"再平一次"的关键
    assert "opened_ts = 0.0" in src


def test_reconcile_is_fail_safe_when_ledger_unavailable():
    """账本读不到时**绝不能**清仓——必须原样返回、只记 skipped。"""
    from backend.services.market_maker import runner as R

    src = inspect.getsource(R.ShadowRunner._reconcile_loaded_states)
    assert "ledger_unavailable" in src or "skipped" in src
    assert "return result" in src


def test_reload_states_exists_and_persists_corrections():
    """运行期对齐：修正后必须 save_states，否则被下一次写覆盖。"""
    from backend.services.market_maker import runner as R

    assert hasattr(R.ShadowRunner, "reload_states")
    src = inspect.getsource(R.ShadowRunner.reload_states)
    assert "save_states" in src


def test_lane_api_exposes_reload_endpoint():
    """端点必须存在，人工平仓/对账后才有办法让进程立刻跟上。"""
    from backend.api import lane_routes

    paths = {getattr(r, "path", "") for r in lane_routes.router.routes}
    assert any(p.endswith("/reload_states") for p in paths), (
        "缺少 /lanes/{lane_id}/reload_states 端点：外部改了账本后进程无从得知")


def test_last_reconcile_initialised_in_constructor():
    """观测字段必须在 __init__ 就存在（巡检/前端会读）。"""
    from backend.services.market_maker import runner as R

    src = inspect.getsource(R.ShadowRunner.__init__)
    assert "last_reconcile" in src


def test_reconcile_corrects_stale_runtime_against_ledger(monkeypatch):
    """核心行为：运行态陈旧 + 账本已平 ⇒ 数量归零且不再武装。"""
    from backend.services.market_maker.runner import ShadowRunner, SymbolState

    r = ShadowRunner(lane_id="t_f301", venue="asterdex", symbols=["X"], equity=300.0)
    st = SymbolState(symbol="X", qty=100.0, avg_px=1.0, avg_mid=1.0, opened_ts=12345.0)
    r.states = {"X": st}
    monkeypatch.setattr(r, "ledger_positions", lambda marks=None: {
        "X": {"symbol": "X", "qty": 0.0}})

    res = r._reconcile_loaded_states(r.states)
    assert res["corrected"], "应报告一次纠正"
    assert abs(st.qty) < 1e-12, "数量必须采用账本（0）"
    assert st.opened_ts == 0.0, "opened_ts 必须清零，否则超时会再平一次"
    assert st.avg_px == 0.0 and st.avg_mid == 0.0


def test_reconcile_does_not_flag_agreement(monkeypatch):
    """无假阳性：两边一致时不得纠正。"""
    from backend.services.market_maker.runner import ShadowRunner, SymbolState

    r = ShadowRunner(lane_id="t_f301b", venue="asterdex", symbols=["X"], equity=300.0)
    st = SymbolState(symbol="X", qty=50.0, avg_px=2.0, avg_mid=2.0, opened_ts=999.0)
    r.states = {"X": st}
    monkeypatch.setattr(r, "ledger_positions", lambda marks=None: {
        "X": {"symbol": "X", "qty": 50.0}})

    res = r._reconcile_loaded_states(r.states)
    assert not res["corrected"], "一致时不应纠正"
    assert st.qty == 50.0 and st.opened_ts == 999.0


def test_reconcile_handles_partial_divergence(monkeypatch):
    """数量不同但账本仍有仓：采用账本数量，并清掉无法确知的成本基准。"""
    from backend.services.market_maker.runner import ShadowRunner, SymbolState

    r = ShadowRunner(lane_id="t_f301c", venue="asterdex", symbols=["X"], equity=300.0)
    st = SymbolState(symbol="X", qty=100.0, avg_px=1.0, avg_mid=1.0, opened_ts=555.0)
    r.states = {"X": st}
    monkeypatch.setattr(r, "ledger_positions", lambda marks=None: {
        "X": {"symbol": "X", "qty": 60.0}})

    r._reconcile_loaded_states(r.states)
    assert abs(st.qty - 60.0) < 1e-12
    assert st.avg_px == 0.0 and st.opened_ts == 0.0
