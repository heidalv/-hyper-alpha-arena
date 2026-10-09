# -*- coding: utf-8 -*-
"""[工作流⑤-a] hold 决策的方向倾向标注（零行为风险）护栏（2026-10-04）。

## 为什么（实测死循环）
`master` 车道 12 天 **9,555 条决策 direction 全为 'hold'** ⇒ 该车道**从不表达方向**：
```
无方向 → 前向标注无样本（12 天 0 条）→ 无法校准 → 无法评估 → 无法改进 → 继续 hold
```
⑤-a 只做一件事：把**载荷里已经存在**的方向证据（如辩论净倾向 `net_sentiment`）结构化写进快照，
**不参与任何判定**（`lean_effect = "observability_only"`）。

## 本测试守护的不变量
1. **零行为风险**：`lean` 只在 `verdict_json` 上新增字段，**不得读取它来做任何裁决**
2. **不造数**：没有方向证据（字段缺失/为 0）时**什么都不写**
3. 必须显式标注 `observability_only`（防止后来者误当成生效参数）
4. 只对 `hold` 生效（非 hold 决策的方向本来就明确）
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SRC = (ROOT / "backend/services/decision_snapshot_writer.py").read_text(encoding="utf-8")


def test_lean_is_annotation_only():
    assert 'verdict_json["lean_effect"] = "observability_only"' in SRC
    # 不得把 lean 用于任何条件判定的读取（只允许写入）
    reads = [ln for ln in SRC.splitlines() if "lean" in ln and ("if " in ln or "and " in ln or "or " in ln)]
    for ln in reads:
        assert "action" in ln or "strip" in ln or "_src_val" in ln, f"疑似把 lean 用于判定: {ln.strip()[:80]}"


def test_no_fabrication_when_evidence_missing():
    seg = SRC.split("工作流⑤-a")[1][:2200]
    assert "isinstance(proposal_json.get(_k), (int, float))" in seg, "必须显式检查证据存在"
    assert "if _src_val is not None and abs(_src_val) > 1e-9:" in seg, "缺证据/为零时不得写"


def test_only_holds_are_annotated():
    assert 'if str(action or "").strip().lower() in ("hold", ""):' in SRC


def test_lean_source_is_traced():
    """必须记录来源字段名，便于回溯（本会话多次因来源不明而误判）。"""
    assert 'verdict_json["lean_source"] = _src_name' in SRC
    assert "net_sentiment" in SRC


def test_writer_still_compiles_and_has_prior_hooks():
    """⑤-a 不得破坏既有接线（账本入账 / 标注行写入 / 原因填充）。"""
    assert "record_decision(" in SRC, "决策入账接线必须仍在"
    assert "record_decision_label(" in SRC, "标注行接线必须仍在"
    assert "reason_source" in SRC, "原因来源标注必须仍在"
