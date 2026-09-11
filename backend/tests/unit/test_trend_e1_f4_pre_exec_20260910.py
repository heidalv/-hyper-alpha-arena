# -*- coding: utf-8 -*-
"""[2026-09-10 §51] E1 live 门禁「执行前评估 + 真会拦 + fail-closed」契约测试。

背景（§51.1，三准则核查结论）：
  - `trend_e1_f4_gate.assert_live_allowed()` 修复前**零调用点**（死包装），
    而它的 docstring 声称 "trend_e1_engine live 路径调用"；
  - 唯一调用点 `scheduled_job()` 在 `run_daily()` **之后**才评估 F4，且只追加一条
    note —— 结构上不可能拦住任何执行；
  - `TREND_E1_LIVE_ASTER` 在执行链上无消费方 → 打开它是静默无效（假开关）。

本测试先写后改（本轮顺序），锁五件事：
  1. live 意图时**先**过门再执行（调用顺序）；
  2. live 未请求时**不**评估门禁（prod 路径零副作用：不写 f4_gate_latest.json）；
  3. 门禁未过 → `f4_blocked=True` 且执行语义仍为 paper（不改变既有行为）；
  4. 门禁异常 → fail-closed（不得静默放行）；
  5. 源码护栏：`assert_live_allowed` 有真实调用点；`E1_LIVE_ROUTING_IMPLEMENTED`
     常量与 `paper_engine.place_order` 无实盘路由的代码事实一致。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

import backend.services.trend_e1_engine as E  # noqa: E402
import backend.services.trend_e1_f4_gate as G  # noqa: E402
from backend.services.paper_trading_engine import paper_engine  # noqa: E402


@pytest.fixture()
def clean_env(monkeypatch):
    """默认关闭 live 意图，避免 .env 串扰。"""
    monkeypatch.setenv("TREND_E1_LIVE_ASTER", "false")
    return monkeypatch


def _fake_run_daily(calls, result=None):
    def _run(*, account_ids=None, execute=None, targets=None):
        calls.append("run_daily")
        return dict(result or {"as_of_bar": "2026-09-09", "execute": False, "accounts": {}})
    return _run


def test_gate_evaluated_before_execution(clean_env):
    """live 意图时：门禁必须在 run_daily **之前**被调用。"""
    clean_env.setenv("TREND_E1_LIVE_ASTER", "true")
    calls = []

    def _gate():
        calls.append("gate")
        return {"allowed": True, "gate": {"passed": True, "live_allowed": True}}

    clean_env.setattr(G, "assert_live_allowed", _gate)
    clean_env.setattr(E, "run_daily", _fake_run_daily(calls))

    out = E.scheduled_job()
    assert calls == ["gate", "run_daily"], calls
    assert out["f4_live_requested"] is True


def test_gate_not_evaluated_when_live_not_requested(clean_env):
    """prod 默认（TREND_E1_LIVE_ASTER=false）：不评估门禁、不落盘、字段为 False。"""
    calls = []

    def _gate():  # pragma: no cover - 不应被调用
        calls.append("gate")
        raise AssertionError("live 未请求时不得评估门禁（会产生落盘副作用）")

    clean_env.setattr(G, "assert_live_allowed", _gate)
    clean_env.setattr(E, "run_daily", _fake_run_daily(calls))

    out = E.scheduled_job()
    assert calls == ["run_daily"], calls
    assert out["f4"] is None
    assert out["f4_live_requested"] is False
    assert out["f4_blocked"] is False
    assert out["f4_reason"] is None


def test_gate_failure_blocks_live_intent_and_keeps_paper(clean_env, caplog):
    """门禁未过 → 记 f4_blocked，执行语义不变（仍 paper），且有 WARNING 可追溯。"""
    clean_env.setenv("TREND_E1_LIVE_ASTER", "true")
    calls = []

    def _gate():
        calls.append("gate")
        return {"allowed": False, "gate": {"passed": False, "live_allowed": False,
                                           "checks": [{"name": "run_days", "ok": False},
                                                      {"name": "halt_tests", "ok": True}]}}

    clean_env.setattr(G, "assert_live_allowed", _gate)
    clean_env.setattr(E, "run_daily", _fake_run_daily(calls, {"execute": False, "accounts": {}}))

    with caplog.at_level("WARNING"):
        out = E.scheduled_job()
    assert out["f4_blocked"] is True
    assert out["f4"]["passed"] is False
    assert "未过 F4" in (out.get("note") or "")
    assert out["f4_reason"] == "f4_failed:run_days"
    assert any("未过门" in (r.getMessage() or "") for r in caplog.records), \
        [r.getMessage() for r in caplog.records]


def test_gate_passes_but_live_routing_unimplemented_is_loud(clean_env, caplog):
    """F4 过门 ≠ 能实盘：本构建无 E1 实盘路径，必须显式告警而非静默无效。"""
    clean_env.setenv("TREND_E1_LIVE_ASTER", "true")
    calls = []
    clean_env.setattr(G, "assert_live_allowed",
                      lambda: {"allowed": True, "gate": {"passed": True, "live_allowed": True}})
    clean_env.setattr(E, "run_daily", _fake_run_daily(calls))

    with caplog.at_level("WARNING"):
        out = E.scheduled_job()
    assert out["f4_blocked"] is True
    assert out["f4"]["live_allowed"] is True
    assert out["f4_reason"] == "live_routing_unimplemented"
    assert any("没有" in (r.getMessage() or "") and "实盘" in (r.getMessage() or "")
               for r in caplog.records), [r.getMessage() for r in caplog.records]


def test_gate_exception_is_fail_closed(clean_env, caplog):
    """门禁抛异常 → 视为未过门（fail-closed），绝不放行 live 意图。"""
    clean_env.setenv("TREND_E1_LIVE_ASTER", "true")
    calls = []

    def _boom():
        raise RuntimeError("drift 文件损坏")

    clean_env.setattr(G, "assert_live_allowed", _boom)
    clean_env.setattr(E, "run_daily", _fake_run_daily(calls))

    with caplog.at_level("WARNING"):
        out = E.scheduled_job()
    assert calls == ["run_daily"], calls
    assert out["f4_blocked"] is True
    assert out["f4"] is None  # 门禁没跑完 → 不伪造 passed 字段
    assert str(out["f4_reason"]).startswith("gate_error:")


def test_assert_live_allowed_has_real_callsite():
    """源码护栏：死包装不得复发——门禁函数必须被引擎源码引用。"""
    src = inspect.getsource(E)
    assert "assert_live_allowed(" in src, "assert_live_allowed 再次变成零调用点（死包装）"
    assert "_pre_exec_live_gate()" in inspect.getsource(E.scheduled_job)


def test_live_routing_constant_matches_code_fact():
    """源码护栏：E1 无实盘路由这一事实与常量一致（防止假开关悄悄变成真下单）。"""
    body = inspect.getsource(paper_engine.place_order)
    markers = [m for m in ("trading_mode", "is_live", "account_type", "get_executor", "LiveExecutor")
               if m in body]
    assert not markers, f"place_order 出现实盘路由标记 {markers} → E1_LIVE_ROUTING_IMPLEMENTED 该改 True 了"
    assert E.E1_LIVE_ROUTING_IMPLEMENTED is False
