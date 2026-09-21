# -*- coding: utf-8 -*-
"""[轮158 2026-09-21] 辩论轮被 max_tokens 截断 —— 观察项 + 修复棘轮。

## 现场（reports/_probe193_debate_truncation.txt）
```
09-21 15:56:41 error out=900 in=1492 nojson=True  ← 上限
09-21 15:56:32 error out=900 in=1208 nojson=True  ← 上限
09-21 15:56:01 error out=900 in=1204 nojson=True  ← 上限
09-21 15:57:02 ok    out=433 in=754
09-21 15:56:53 ok    out=775 in=1502
近 3h：error n=175 avg_out=754 max_out=1510 / ok n=46 avg_out=416
```
每一条"输出中无可解析 JSON"的 `output_tokens` 都**正好等于上限 900** ⇒ 回答被截断成半截 JSON；
成功的行只需 400~775。辩论每轮要写 argument+evidence+counterargument+weakness +
三周期 horizons，且 DeepSeek 的 thinking token 也计入 completion_tokens ⇒ 900 不够。

## 两处修复
1. **抬高上限**：`MIDLONG_DEBATE_MAX_OUTPUT_TOKENS` 代码默认 900 → 2500（`.env` 同步；
   max_tokens 只是上限、不预扣费）。
2. **让"截断"可见**：`RawCompletion` 增加 `finish_reason`，DeepSeek 传输回填；
   `extract_json` 失败且 `finish_reason=="length"` 时，账本错误写成
   "输出被 max_tokens 截断（finish_reason=length）" 而不是笼统的"无可解析 JSON"。
   （此前正是因为没有这个字段，定位绕了好几轮。）
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.analysis import model_gateway as MG  # noqa: E402


def test_rawcompletion_has_finish_reason_default():
    rc = MG.RawCompletion(text="x")
    assert rc.finish_reason == ""


def test_deepseek_transport_fills_finish_reason(monkeypatch):
    """传输必须把供应商的 finish_reason 带出来（截断可见的前提）。"""
    from types import SimpleNamespace

    import backend.services.llm_config_service as LCS

    def _fake(cfg, messages, **kw):
        return {"choices": [{"message": {"content": '{"a":1}'}, "finish_reason": "length"}],
                "model": "deepseek-flash", "usage": {"prompt_tokens": 1, "completion_tokens": 2}}

    monkeypatch.setattr(LCS, "call_llm_api_sync", _fake)
    t = MG.DeepSeekTransport()
    monkeypatch.setattr(t, "_resolve", lambda: SimpleNamespace(
        id=0, name="t", provider="deepseek", model="deepseek-flash",
        base_url="https://api.deepseek.com", api_key="k"))
    rc = t.complete("s", "u", max_tokens=10, temperature=0, timeout_s=5, task="mlto_debate")
    assert rc.finish_reason == "length"


def test_truncated_output_is_named_in_the_ledger(monkeypatch):
    """截断导致 JSON 不可解析时，错误文案必须点名"截断"（而不是笼统的不可解析）。"""
    from types import SimpleNamespace

    class _Stub(MG.Transport):
        name = "deepseek"

        def configured(self):
            return True, "stub"

        def model_name(self):
            return "deepseek-flash"

        def complete(self, system, user, *, max_tokens, temperature, timeout_s, task, images=None):
            return MG.RawCompletion(text='{"argument": "被截断的半截', input_tokens=1,
                                    output_tokens=int(max_tokens), model="deepseek-flash",
                                    finish_reason="length")

    class _Q:
        budget = SimpleNamespace(max_output_tokens=2000)

        def check(self, *a, **k):
            return SimpleNamespace(ok=True, action="", reason="")

        def record(self, *a, **k):
            return None

    gw = MG.ModelGateway(transports={"deepseek": _Stub()}, quota=_Q())
    res = gw.call("mlto_debate", "sys 只输出 JSON", "user", transport="deepseek", max_output_tokens=100)
    assert not res.ok
    assert "截断" in (res.error or ""), res.error
    assert "finish_reason=length" in (res.error or "")


def test_debate_cap_default_is_roomy_enough():
    """代码默认上限必须留出空间（实测成功答案 400~775 tok + thinking）。"""
    src = (ROOT / "backend/services/mlto/brain_debate.py").read_text(encoding="utf-8-sig")
    assert '_num("MIDLONG_DEBATE_MAX_OUTPUT_TOKENS", 2500)' in src, \
        "辩论上限默认值又回到 900（会被截断）"
