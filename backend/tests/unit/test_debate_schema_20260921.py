# -*- coding: utf-8 -*-
"""[轮157 2026-09-21] 牛熊辩论的输出契约必须**注册**，否则被公共契约顶掉。

## 根因（实测）
`ModelGateway.call` 用 `schemas.validate(schema_task or task, obj)` 校验，并会把
`schemas.output_contract(task)` 注入 **system prompt**。`mlto_debate` 此前**未注册**
（审计确认：它是唯一一个），于是：
  · 校验退回 `COMMON_REQUIRED`（direction/strength/confidence/key_factors/summary）；
  · 注入的契约也是这套 —— 与辩论 prompt 自己的 `argument/confidence/evidence/...` 冲突。
后果（近 12 行账本 + 8h 统计）：
  · 牛/熊按辩论 prompt 作答 ⇒ 被判 schema 失败（`status=error`；MiniMax 时期 444/560=79%）；
  · 风控轮反过来照抄注入的公共契约（summary/direction/strength）⇒ 校验通过，但辩论解析器
    要的 `argument` 缺失 ⇒ 悄悄退化成规则中性。

## 修复后实测（`reports/_probe191_debate_after_schema.txt`）
```
15:46:19 error schema_errors=["缺少字段 direction","strength","key_factors","summary"]
         keys=['argument','evidence','horizons','confidence']
15:47:33 ok    schema_errors=[]  keys=['argument','evidence','horizons','weakness','confidence','counterargument']
完整辩论：verdict=reduce used_llm=true primary_verdict=reduce
         horizon_verdicts={intraday:reduce, swing:reduce, trend:reduce}
```
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.analysis import schemas as S  # noqa: E402

BULL_BEAR = {
    "argument": "中期结构偏多，回踩 EMA 后仍有延续空间",
    "confidence": 0.62,
    "evidence": ["4h EMA9>EMA21", "资金费温和"],
    "counterargument": "对方说的超买尚未形成背离",
    "weakness": "缺宏观锚定",
    "horizons": {
        "intraday": {"stance": "neutral", "confidence": 0.3, "argument": "日内震荡"},
        "swing": {"stance": "long", "confidence": 0.6, "argument": "中期偏多"},
        "trend": {"stance": "long", "confidence": 0.55, "argument": "长期向上"},
    },
}
RISK = {"argument": "风险可接受但信心有限", "confidence": 0.45}
OLD_COMMON_SHAPE = {"summary": "观望", "direction": "neutral", "strength": 3,
                    "confidence": 0.5, "key_factors": ["x"]}


def test_debate_task_is_registered():
    assert "mlto_debate" in S.TASK_SCHEMAS, "辩论 task 未注册 → 会被公共契约顶掉"
    req = S.TASK_SCHEMAS["mlto_debate"]["required"]
    assert set(req) == {"argument", "confidence"}, req


def test_bull_bear_and_risk_outputs_validate():
    for label, obj in (("bull/bear", BULL_BEAR), ("risk", RISK)):
        ok, errs = S.validate("mlto_debate", obj)
        assert ok, f"{label} 输出应通过：{errs}"


def test_common_shape_is_now_rejected():
    """公共形状（direction/strength/summary）不再能冒充辩论输出 —— 这是回归护栏。"""
    ok, errs = S.validate("mlto_debate", OLD_COMMON_SHAPE)
    assert not ok and any("argument" in e for e in errs), errs


def test_injected_contract_matches_the_debate_not_the_common_one():
    """注入 system 的契约必须是辩论形状，且不得再要求 direction/strength/key_factors/summary。"""
    txt = S.output_contract("mlto_debate")
    for must in ("argument", "confidence", "evidence", "counterargument", "weakness",
                 "horizons", "stance"):
        assert must in txt, f"契约缺字段 {must}"
    for must_not in ("key_factors", "direction", "strength", "summary"):
        assert f'"{must_not}"' not in txt, f"契约仍在要求公共字段 {must_not}（会与辩论 prompt 冲突）"


def test_contract_mark_present_so_double_injection_is_avoided():
    assert S.CONTRACT_MARK in S.output_contract("mlto_debate")


def test_debate_call_via_gateway_validates_ok():
    """端到端（替身传输）：走 ModelGateway.call('mlto_debate') 时 ok 必须为 True。"""
    from types import SimpleNamespace

    from backend.services.analysis import model_gateway as MG

    class _Stub(MG.Transport):
        name = "deepseek"

        def configured(self):
            return True, "stub"

        def model_name(self):
            return "deepseek-flash"

        def complete(self, system, user, *, max_tokens, temperature, timeout_s, task, images=None):
            assert "horizons" in system, "注入的契约里应有 horizons（否则模型不知道要分周期）"
            assert '"direction"' not in system, "注入的契约不该再要求 direction"
            return MG.RawCompletion(text=__import__("json").dumps(BULL_BEAR, ensure_ascii=False),
                                    input_tokens=1, output_tokens=1, model="deepseek-flash")

    class _Q:
        budget = SimpleNamespace(max_output_tokens=2000)

        def check(self, *a, **k):
            return SimpleNamespace(ok=True, action="", reason="")

        def record(self, *a, **k):
            return None

    gw = MG.ModelGateway(transports={"deepseek": _Stub()}, quota=_Q())
    res = gw.call("mlto_debate", "你是辩论参与者，只输出 JSON。", "给出你的论点", transport="deepseek")
    assert res.ok, res.error
    assert res.schema_errors == []
    assert (res.json or {}).get("argument")
