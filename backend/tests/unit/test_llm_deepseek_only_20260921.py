# -*- coding: utf-8 -*-
"""[轮155 2026-09-21] LLM 全部走 deepseek-flash + 去掉双模型交叉验证（用户指令）。

用户原话（三句）：
  1) 「现在llm，全部换成deepseek fl 吧 不用minimax了」
  2) 「不要用pro」
  3) 「那就去掉这个双模型验证，这个也是累赘」

## 为什么"只用 flash"必须连带动验证协议（实测，不是推断）
`reports/_probe171_dual_call_reality.txt` / `_probe172_single_model_live.txt`：
  · 单票（一票）        → status=degraded consensus_score=0.375 accepted=False
  · deepseek+deepseek   → 网关自己打印「同为模型 deepseek-v4-flash → 视作单票」
  · 阈值 CONSENSUS_THRESHOLD=0.7 是**两票一致性**的判据；DeepSeek 侧 `/models` 只有
    `deepseek-flash` 与 `deepseek-v4-pro`，用户只用前者 ⇒ 没有第二个模型可当第二票。
故按用户指令改为**单模型模式**：一次调用即结论。

## 本测试钉住的四件事
  ① 代码默认与部署 `.env` 里都不再有任何 minimax 传输被选中；
  ② 单模型模式下：合法 JSON → status=ok、consensus_score=自报 confidence、accepted 按门槛；
  ③ 回滚口径（ANALYSIS_SINGLE_MODEL_MODE=false）仍保留双票协议与单票 ≤0.6 的旧防线；
  ④ 硬编码传输顺序表（news_annotate / smart_money / quota）也不再点名 minimax。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.analysis import model_gateway as MG  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("ANALYSIS_SINGLE_MODEL_MODE", "ANALYSIS_SINGLE_MODEL_MIN_CONF",
              "ANALYSIS_PRIMARY_TRANSPORTS", "ANALYSIS_FALLBACK_TRANSPORTS",
              "ANALYSIS_ARBITER_TRANSPORT"):
        monkeypatch.delenv(k, raising=False)
    yield


def test_code_defaults_are_deepseek_only():
    """不设任何 env 时，默认链路必须是 deepseek，且不得回落 minimax。"""
    assert MG.ModelGateway.primary_names() == ["deepseek"]
    assert MG.ModelGateway.arbiter_name() == "deepseek"
    assert "minimax" not in MG.ModelGateway.fallback_names()
    assert MG.ModelGateway.fallback_names()[0] == "deepseek"


def test_single_model_mode_default_on_and_threshold_knob():
    assert MG.single_model_mode() is True, "用户指令：去掉双模型验证"
    assert MG.single_model_min_conf() == pytest.approx(0.0)


def test_deployed_env_has_no_minimax(monkeypatch):
    """.env（部署口径）里主/备/仲裁都不得出现 minimax，且单模型模式为开。"""
    from dotenv import load_dotenv

    load_dotenv(str(ROOT / ".env"), override=False)
    import os

    for k in ("ANALYSIS_PRIMARY_TRANSPORTS", "ANALYSIS_FALLBACK_TRANSPORTS",
              "ANALYSIS_ARBITER_TRANSPORT"):
        assert "minimax" not in (os.getenv(k) or ""), f"{k} 仍指向 minimax: {os.getenv(k)}"
    assert (os.getenv("ANALYSIS_SINGLE_MODEL_MODE") or "").lower() == "true"
    assert (os.getenv("ANALYSIS_PRIMARY_TRANSPORTS") or "").strip() == "deepseek"


def test_no_minimax_in_hardcoded_transport_lists():
    """硬编码的**传输选择**处不得再点名 minimax（注释/文档里的历史说明不算）。

    判据：同一行里 minimax 出现在字符串字面量中，且该行是"选传输"的语法位置
    （`for tr in (` / `transport=` / `_env(` / `images_for=` / `primaries=`）。
    """
    import re

    pat = re.compile(r'["\']minimax["\']')
    # 注意：**不**把 `TRANSPORTS = (...)` 这种"可选传输注册表"算进来 —— MiniMaxTransport 有意
    # 保留注册以便回滚；要管的是"谁被选中去调用"。
    selectors = ("for tr in", "transport=", "_env(", "images_for=", "primaries=")
    for rel in ("backend/services/news_intelligence_service.py",
                "backend/services/events/smart_money.py",
                "backend/services/analysis/quota_guard.py",
                "backend/services/analysis/tasks.py",
                "backend/services/analysis/model_gateway.py"):
        src = (ROOT / rel).read_text(encoding="utf-8-sig")
        for i, ln in enumerate(src.splitlines(), 1):
            if not pat.search(ln):
                continue
            if any(s in ln for s in selectors):
                pytest.fail(f"{rel}:{i} 仍把 minimax 当调用目标: {ln.strip()[:120]}")


class _StubTransport:
    """最小替身：返回固定 JSON，记录被调用次数。"""

    def __init__(self, name: str, payload: dict, model: str = "deepseek-v4-flash"):
        self.name = name
        self._payload = payload
        self._model = model
        self.calls = 0

    def configured(self):
        return True, f"model={self._model}"

    def model_name(self):
        return self._model

    def status(self):
        return {"name": self.name, "configured": True, "detail": f"model={self._model}"}

    def complete(self, system, user, *, max_tokens, temperature, timeout_s, task, images=None):
        from backend.services.analysis.model_gateway import RawCompletion

        self.calls += 1
        return RawCompletion(text=__import__("json").dumps(self._payload), input_tokens=10,
                             output_tokens=10, model=self._model, tokens_estimated=False)


def _gw(payload: dict):
    stub = _StubTransport("deepseek", payload)

    class _Q:
        budget = type("B", (), {"max_output_tokens": 2000})()

        def check(self, *a, **k):
            return type("D", (), {"ok": True, "action": "", "reason": ""})()

        def record(self, *a, **k):
            return None

        def snapshot(self):
            return {}

    gw = MG.ModelGateway(transports={"deepseek": stub}, quota=_Q())
    return gw, stub


def _payload(direction="long", conf=0.62):
    return {"direction": direction, "confidence": conf, "strength": 3, "recommend_open": True,
            "should_close": False, "key_factors": ["a"], "summary": "s",
            "invalidation": {"price": 1.0, "condition": "c"}, "missing_evidence": [],
            "sl_pct": 0.02, "tp_pct": 0.04, "thesis_summary": "t"}


def test_single_model_mode_accepts_on_model_confidence():
    gw, stub = _gw(_payload())
    cres = gw.dual_call("midlong_thesis", "sys", "user", primaries=["deepseek"])
    assert stub.calls == 1, "单模型模式只应调用一次"
    assert cres.status == "ok"
    assert cres.consensus_score == pytest.approx(0.62), "单模型下共识分=模型自报置信度"
    assert cres.accepted is True
    assert (cres.comparison or {}).get("verification") == "disabled"
    assert any("无第二方验证" in n for n in (cres.notes or []))


def test_single_model_min_conf_knob(monkeypatch):
    monkeypatch.setenv("ANALYSIS_SINGLE_MODEL_MIN_CONF", "0.8")
    gw, _stub = _gw(_payload(conf=0.62))
    cres = gw.dual_call("midlong_thesis", "sys", "user", primaries=["deepseek"])
    assert cres.status == "ok" and cres.accepted is False, "门槛 0.8 时 0.62 不得入账"


def test_rollback_keeps_dual_protocol_cap(monkeypatch):
    """回滚口径：关掉单模型模式后，单票仍必须是 degraded 且 ≤0.6（旧防线不丢）。"""
    monkeypatch.setenv("ANALYSIS_SINGLE_MODEL_MODE", "false")
    gw, stub = _gw(_payload())
    cres = gw.dual_call("midlong_thesis", "sys", "user", primaries=["deepseek"])
    assert stub.calls == 1
    assert cres.status == "degraded"
    assert cres.consensus_score <= 0.6
    assert cres.accepted is False
