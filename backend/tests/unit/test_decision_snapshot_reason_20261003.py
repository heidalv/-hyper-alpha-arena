# -*- coding: utf-8 -*-
"""[2026-10-03] 决策快照"拦截原因从未落库"修复护栏。

## 实测量化（近 30 天 `decision_snapshots`，alpha_analytics）
· 12,219 条快照 / **11,990 条未执行（执行率 1.89%）**；
· `gate_blocks_json` **全表为空**、`evaluate_verdict_json.reason`/`code_reason` **是空串**
  ⇒ **无法从数据上归因是哪一层挡的**（block-pattern 学习缺结构化输入）。
· 按车道执行率：master/long 4,161 → **0%**、master/mid 4,138 → **0%**、master/short 1,530 → **0%**、
  swing_independent/mid 1,950 → 11.4%、trend_follow_independent/long 312 → 1.9%。
· master 车道 action 分布：**hold 9,806（平均置信 40.3）**、close 19、reduce 4 —— **从未出现 open**
  ⇒ 0% 不是"被闸门拦"，而是**该车道自己从不建议开仓**；且其中 **1,952 条置信 ≥50（门槛线以上）
  仍 0 执行**，更说明必须有结构化原因才能继续归因。

## 修复
`DecisionSnapshotWriter.build`：调用方没给结构化原因时，按**真实已有材料**补齐并显式标注来源——
`evaluate_verdict.{code_reason,reason,gate_reason}` → `proposal.{block_reason,code_reason,reason}`
→ `ai_reasoning` 前 120 字（标 `reason_source="derived_from_reasoning"`）；
未执行且无 `gate_blocks` 时合成一条 `{layer, rule, reason, auto_fill: true}`。
**不伪造数据**：`auto_fill`/`reason_source` 让"自动补的"与"闸门真回执"可区分。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SRC_WRITER = (ROOT / "backend/services/decision_snapshot_writer.py").read_text(encoding="utf-8")


def test_writer_fills_reason_and_gate_blocks():
    assert "reason_source" in SRC_WRITER, "必须标注原因来源"
    # [2026-10-03 ②] 现在写入的是 `_final`（标签优先于自由文本），因此断言同步
    assert 'verdict_json["code_reason"] = str(_final)[:200]' in SRC_WRITER
    assert '"auto_fill": True' in SRC_WRITER, "合成的 gate_blocks 必须带 auto_fill 标记"
    assert 'executed is not True' in SRC_WRITER, "只在未执行时合成 gate_blocks"


def test_writer_does_not_fabricate():
    """不能凭空造 gates：优先调用方材料，最后才用 ai 文本，且必须标注来源。"""
    seg = SRC_WRITER.split("[2026-10-03 修复 · 决策拦截原因从未落库]")[1].split("snap = DecisionSnapshot(")[0]
    # 按**代码标记**比较（注释里也会出现同一批字样，不能用裸词比较）
    assert seg.index('verdict_json.get("gate_reason")') < seg.index('_src = "derived_from_reasoning"')
    assert 'verdict_json.get("gate_blocks")' in seg, "已有真实 gate_blocks 时不得覆盖"


def test_build_produces_structured_reason_end_to_end():
    """行为级：调用方只给自由文本时，快照必须带上可查询的结构化原因。"""
    from backend.services.decision_snapshot_writer import decision_snapshot_writer as W

    snap = W.build(
        session_id="s_test", strategy_id="st_test", symbol="BTC", tier="mid", action="hold",
        confidence=35, reasoning="证据不足不开仓（测试用）。",
        proposal={"source_lane": "master"},
        evaluate_verdict={"allowed": False, "reason": "", "code_reason": "", "layer": "master", "rule": "master"},
        executed=False, source_lane="master",
    )
    v = snap.evaluate_verdict_json or {}
    assert v.get("code_reason"), "code_reason 必须被补齐（此前恒为空串）"
    assert v.get("reason_source") == "derived_from_reasoning"
    gb = snap.gate_blocks_json or []
    assert gb and gb[0].get("auto_fill") is True
    assert "证据不足" in str(gb[0].get("reason"))
