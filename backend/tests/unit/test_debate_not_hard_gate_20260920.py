# -*- coding: utf-8 -*-
"""[轮136 2026-09-20] v5gate 溯源结论：辩论折减写回 confidence ⇒ 意外触发**硬门槛**。

## 实测（`[V5Gate] BLOCK` 日志）
```
symbol=UNI action=buy rule=confidence detail=置信度 28% < 有效门槛 30%(生效门=trend 成熟度=warmup 松紧+20)
symbol=BTC action=buy rule=confidence detail=置信度 25% < 有效门槛 30%(…)
```
`llm_conviction` 同时是**门槛用的 confidence**（maybe_open 传 `confidence=llm_conviction`）。
辩论 reject→×0.6 把 40 折到 24 ⇒ 提案以 25~28% 撞 30% 门槛 ⇒ **被 v5gate 硬拦 20 次**：
"辩论的去风险"变成了"事实否决"，与架构分工相反（否决权属风控官）。

## 本文件守什么
1. **默认不写回**：`MIDLONG_DEBATE_APPLY` 默认 false（`apply_conviction_effect` 返回 "off"）；
2. brain.py 里那段**不得**把折减写回 `dto.llm_conviction`（源码级守卫 + 注释说明原因）；
3. 辩论仍必须被记录（裁决/分周期/折减说明进日志与论题事件）——只是不改变门槛置信度；
4. 显式开启（APPLY=true）时行为仍然可用（供"规模链已消费"之后再启用）。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.mlto import brain_debate as BD  # noqa: E402


def test_apply_defaults_to_record_only(monkeypatch):
    monkeypatch.delenv("MIDLONG_DEBATE_APPLY", raising=False)
    c, note = BD.apply_conviction_effect(40.0, {"primary_horizon": "intraday",
                                                "primary_verdict": "reject"})
    assert c == 40.0 and note == "off", \
        "默认必须只记录：写回 confidence 会撞 [V5Gate] rule=confidence 硬门槛"


def test_apply_still_available_when_explicitly_enabled(monkeypatch):
    monkeypatch.setenv("MIDLONG_DEBATE_APPLY", "true")
    monkeypatch.setenv("MIDLONG_DEBATE_REJECT_MULT", "0.6")
    c, note = BD.apply_conviction_effect(40.0, {"primary_horizon": "intraday",
                                                "primary_verdict": "reject"})
    assert c == 24.0 and "日内" in note


def test_brain_does_not_write_debate_result_back_to_conviction():
    """源码守卫：brain.py 里**不得**出现 `dto.llm_conviction = int(_new_conv)` 这种写回。"""
    src = (ROOT / "backend/services/mlto/brain.py").read_text(encoding="utf-8", errors="replace")
    assert "dto.llm_conviction = int(_new_conv)" not in src, \
        "辩论折减又被写回 confidence（会再次触发 v5gate 硬拦）"
    assert "辩论折减记录（未写回 confidence）" in src, "缺少'只记录'的显式日志"
    # 仍然必须调用它（记录用），不能被整段摘掉
    names = {n.func.attr if isinstance(n.func, ast.Attribute) else
             (n.func.id if isinstance(n.func, ast.Name) else "")
             for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)}
    assert "apply_conviction_effect" in names, "辩论折减记录被整段摘掉了"


def test_v5gate_confidence_rule_reads_conviction_directly():
    """把"门槛读 conviction"这一事实钉住：一旦有人改这条链，本测试会提醒复核本文件。"""
    src = (ROOT / "backend/services/mlto/brain.py").read_text(encoding="utf-8", errors="replace")
    assert "confidence=int(thesis.llm_conviction or 50)" in src, \
        "maybe_open 传给门槛的 confidence 不再取 llm_conviction ⇒ 请复核辩论折减是否可安全写回"
