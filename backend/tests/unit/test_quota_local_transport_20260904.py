# -*- coding: utf-8 -*-
"""本地推理传输的配额豁免（2026-09-04）。

背景：QuotaGuard 原本对所有传输一视同仁（record 的理由是"供应商侧同样扣减"），
但本地 Ollama 不走任何供应商 Key，没有 5h 窗 / 周配额 / 峰时倍率。后果是云端配额一
耗尽（实测 DeepSeek event 类当天 20/20 用满），免费的本地兜底票会被一起拦掉 ——
恰恰在最需要兜底的时候失效，等于白加这条传输。

锁两件事：
  1. 本地传输不受次数配额约束，云端传输照旧受约束
  2. token 上限对本地传输仍然生效（那是防塞爆模型窗口，与配额无关）
"""
from __future__ import annotations

import pytest

from backend.services.analysis import quota_guard as qg


@pytest.fixture()
def guard(monkeypatch, tmp_path):
    monkeypatch.setenv("ANALYSIS_LOCAL_TRANSPORTS", "ollama")
    monkeypatch.setenv("ANALYSIS_LIGHT_DAILY_PER_MODEL", "3")
    monkeypatch.setenv("ANALYSIS_5H_CALLS_PER_MODEL", "3")
    g = qg.QuotaGuard()
    # 屏蔽落库：窗口计数是内存态，落库与本测试无关。
    # [2026-09-04] 此处**不得**加 raising=False —— 之前正是它让这个 patch 在
    # `_persist` 尚不存在时静默失效，每跑一次测试就往生产 llm_quota_usage 写 65 条
    # 假流水，顶掉 DeepSeek 的真实配额。方法名写错时必须让测试当场报错。
    monkeypatch.setattr(qg.QuotaGuard, "_persist", lambda *a, **k: None)
    # 同上：屏蔽从生产库回读用量，否则窗口计数里混入线上真实调用。
    monkeypatch.setattr(qg.QuotaGuard, "_load", lambda self: None)
    return g


def _record(g, transport, n=1):
    for _ in range(n):
        g.record(transport, "gateway_test", model="m", input_tokens=10,
                 output_tokens=10, latency_ms=1, ok=True)


def test_落库入口存在且可被屏蔽(guard, monkeypatch):
    """防回归：record 必须把落库委托给 _persist，否则测试无法屏蔽写库。"""
    assert hasattr(qg.QuotaGuard, "_persist"), "落库必须是独立方法，测试才能屏蔽"
    calls = []
    monkeypatch.setattr(qg.QuotaGuard, "_persist",
                        lambda self, **kw: calls.append(kw))
    guard.record("deepseek", "gateway_test", model="m", input_tokens=1,
                 output_tokens=1, latency_ms=1, ok=True)
    assert len(calls) == 1, "record 应经由 _persist 落库"
    assert calls[0]["transport"] == "deepseek"
    assert calls[0]["task_class"] == "light"


def test_本地传输识别(guard):
    assert qg.is_local_transport("ollama")
    assert qg.is_local_transport("OLLAMA")  # 大小写不敏感
    assert not qg.is_local_transport("deepseek")
    assert not qg.is_local_transport("glm_opencode")


def test_云端传输用满后被降级(guard):
    _record(guard, "deepseek", n=5)
    d = guard.check("deepseek", "gateway_test", est_context_tokens=100)
    assert d.action == "degrade", f"云端用满应降级，实际 {d.action}: {d.reason}"


def test_本地传输不受次数配额约束(guard):
    """同样调 50 次，本地必须依旧放行 —— 这正是原实现的缺陷所在。"""
    _record(guard, "ollama", n=50)
    d = guard.check("ollama", "gateway_test", est_context_tokens=100)
    assert d.action == "allow", f"本地不该被配额拦，实际 {d.action}: {d.reason}"


def test_本地传输不占用窗口计数(guard):
    """本地调用不应写进配额窗口，否则 status 里会出现虚假消耗。"""
    _record(guard, "ollama", n=10)
    counts = guard._counts("ollama", "light", int(__import__("time").time() * 1000))
    assert counts["today"] == 0, f"本地传输不该占窗口，实际 today={counts['today']}"


def test_本地传输仍受token上限约束(guard):
    """token 上限是防塞爆模型窗口的保护，与配额无关，本地同样要挡。"""
    huge = guard.budget.max_context_tokens + 1
    d = guard.check("ollama", "gateway_test", est_context_tokens=huge)
    assert d.action == "reject", f"超长上下文应拒绝，实际 {d.action}"

    d2 = guard.check("ollama", "gateway_test", est_context_tokens=100,
                     max_output_tokens=guard.budget.max_output_tokens + 1)
    assert d2.action == "reject", f"超长输出应拒绝，实际 {d2.action}"
