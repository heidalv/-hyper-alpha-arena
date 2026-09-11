# -*- coding: utf-8 -*-
"""第二条本地票与「防假交叉验证」保护（2026-09-04）。

背景：两条云端主传输长期都不可用（MINIMAX_API_KEY 从未配置、ZAI_CODING_PLAN_API_KEY
实测 401），dual_call 一直退化成单票，而单票的 consensus_score 被硬性压在 ≤0.6，
永远达不到 0.7 阈值 —— 整套深度分析产不出任何可入 signal_ledger 的结论。补一条本地
传输后即可用两个本机模型凑齐两票。

但这带来一个更危险的失败模式：两条本地传输若解析到**同一个模型**，它们的一致毫无
信息量，却会算出很高的共识分并被写进账本。假的交叉验证比没有交叉验证更糟，
因此必须在协议层挡掉。
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest

from backend.services.analysis import model_gateway as mg


class _FakeTransport(mg.Transport):
    """可控的假传输：固定模型名、固定返回。"""

    def __init__(self, name: str, model: str):
        self.name = name
        self._model = model
        self.calls = 0

    def configured(self) -> Tuple[bool, str]:
        return True, "fake"

    def model_name(self) -> str:
        return self._model

    def complete(self, system, user, *, max_tokens, temperature, timeout_s, task):
        self.calls += 1
        return mg.RawCompletion(
            text='{"direction":"neutral","strength":0,"confidence":0.0,"summary":"x"}',
            input_tokens=10, output_tokens=10, model=self._model,
        )


@pytest.fixture()
def gw(monkeypatch):
    """屏蔽账本落库与配额，只考察协议层的传输编排。"""
    monkeypatch.setattr(mg.ModelGateway, "_record", lambda self, *a, **k: "run-x")
    monkeypatch.setattr(mg.QuotaGuard, "check",
                        lambda self, *a, **k: mg.Decision("allow", ""))
    monkeypatch.setattr(mg.QuotaGuard, "record", lambda self, *a, **k: None)
    return mg


def test_ollama2已注册且与ollama不同模型():
    """第二条本地票必须存在，且默认模型与第一条不同。"""
    assert "ollama2" in mg.TRANSPORTS
    g = mg.ModelGateway()
    assert "ollama2" in g.transports
    t1, t2 = g.transports["ollama"], g.transports["ollama2"]
    assert t2._default_model != t1._default_model, (
        "两条本地票默认模型相同 → 交叉验证退化成同模型跑两次"
    )
    assert t2._prefer_db is False, (
        "第二条本地票若走 DB 解析，会查到与第一条相同的 provider=ollama 绑定"
    )


def test_ollama2不走db解析(monkeypatch):
    """prefer_db=False 时模型名只认 env，不受 DB 绑定影响。"""
    monkeypatch.setenv("ANALYSIS_OLLAMA2_MODEL", "some-model:7b")
    t = mg.OllamaTransport(name="ollama2", model_env="ANALYSIS_OLLAMA2_MODEL",
                           default_model="d", prefer_db=False)
    assert t.model_name() == "some-model:7b"


def test_两条主票同模型时退化为单票(gw, monkeypatch):
    """核心保护：同模型的一致不构成交叉验证，必须只发一票。"""
    a = _FakeTransport("ollama", "qwen3:14b")
    b = _FakeTransport("ollama2", "qwen3:14b")  # 故意撞成同一个模型
    g = mg.ModelGateway(transports={"ollama": a, "ollama2": b})

    res = g.dual_call("gateway_test", "sys", "usr", primaries=["ollama", "ollama2"],
                      arbiter="", parallel=False)

    assert a.calls + b.calls == 1, f"同模型应只发一票，实际发了 {a.calls + b.calls} 票"
    assert any("同模型" in n for n in res.notes), f"应留下降级说明，实际 notes={res.notes}"


def test_不同模型时正常发两票(gw):
    a = _FakeTransport("ollama", "qwen3:14b")
    b = _FakeTransport("ollama2", "qwen2.5:7b")
    g = mg.ModelGateway(transports={"ollama": a, "ollama2": b})

    res = g.dual_call("gateway_test", "sys", "usr", primaries=["ollama", "ollama2"],
                      arbiter="", parallel=False)

    assert a.calls == 1 and b.calls == 1, f"应各发一票，实际 a={a.calls} b={b.calls}"
    assert not any("同模型" in n for n in res.notes)


def test_两条本地票强制串行(gw):
    """本机 GPU 推理并发无加速，却会在模型冷启动时踩踏（实测空响应）。"""
    a = _FakeTransport("ollama", "qwen3:14b")
    b = _FakeTransport("ollama2", "qwen2.5:7b")
    g = mg.ModelGateway(transports={"ollama": a, "ollama2": b})

    res = g.dual_call("gateway_test", "sys", "usr", primaries=["ollama", "ollama2"],
                      arbiter="", parallel=True)  # 显式要求并发

    assert any("串行" in n for n in res.notes), (
        f"两条本地票应被改为串行，实际 notes={res.notes}"
    )


def test_混合云端本地时不强制串行(gw):
    """只要有一条是云端传输，并发依旧有意义（网络等待可重叠）。"""
    a = _FakeTransport("deepseek", "deepseek-v4")
    b = _FakeTransport("ollama", "qwen3:14b")
    g = mg.ModelGateway(transports={"deepseek": a, "ollama": b})

    res = g.dual_call("gateway_test", "sys", "usr", primaries=["deepseek", "ollama"],
                      arbiter="", parallel=True)

    assert not any("串行" in n for n in res.notes), f"混合场景不应强制串行: {res.notes}"


def test_本地传输默认名单含两条():
    from backend.services.analysis.quota_guard import local_transports

    names = local_transports()
    assert "ollama" in names and "ollama2" in names, (
        f"两条本地票都应免配额，实际 {names}"
    )
