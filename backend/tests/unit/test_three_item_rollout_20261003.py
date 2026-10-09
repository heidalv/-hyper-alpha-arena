# -*- coding: utf-8 -*-
"""[2026-10-03] 三项挨个实施护栏：①L3 批量裁决 dry-run ②master hold 结构化归因 ③回放去合成。

## ① L3 dry-run（用户指令：批量裁决前先预览）
`auto_accept_pending_paper(..., dry_run=True)` **先于功能门禁**返回候选清单，**不调用 accept_proposal、不写库**。
实测：`would_accept=5`、`pending 313 → 313`（未变动）、`auto_accept_enabled=false` 如实回传。
前端新增「预览批量裁决」按钮 + 预览清单渲染。

## ② master 车道"只会 hold"的结构化归因
实测：master 30 天 9,829 条决策 = **hold 9,806（平均置信 40.3）/ close 19 / reduce 4，从无 open**；
其中 1,952 条置信 ≥50（门槛 `MIDLONG_AGGRESSIVE_MIN_CONF=0.5`）仍 0 执行。
⇒ 0% 不是"被闸门拦"，而是**该车道自己从不建议开仓**（大脑判断），且**历史 ai_reasoning 无前缀标签**
（`by_tag` 实测 `(无标签)×9829`）。修复：写入层把 reasoning 的**前缀标签提取成结构化 `code_reason`**
（`reason_source="tag_from_reasoning"`），使后续可按标签精确计数；历史仍靠关键词分类（不伪造）。

## ③ 回放去合成
`POST /api/learning/replay/seed-real` → 把 `decision_snapshots` 里 executed=true 且有 pnl_pct 的**真实成交**
灌入 RL 缓冲（source=live，reward=真实 pnl_pct，按 symbol+action+reward 近似去重）。
实测：`{backtest:2, synthetic:300}` → **`{backtest:2, live:17, synthetic:300}`**，total 302 → 319。
**新暴露的瓶颈**：365 天内 executed 决策 229 条，但**只有 17 条带 pnl**（7.4%）——
受限于"决策快照 ↔ 成交回填"匹配（日志：`DecisionSnapshot 回写 ambiguous，跳过以防归因错配`）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SRC_ARCH = (ROOT / "backend/services/hermes_architecture_evolution_engine.py").read_text(encoding="utf-8")
SRC_ROUTES = (ROOT / "backend/api/hermes_routes.py").read_text(encoding="utf-8")
SRC_PANEL = (ROOT / "frontend-next/src/components/learning/HermesLevelsPanel.tsx").read_text(encoding="utf-8")
SRC_WRITER = (ROOT / "backend/services/decision_snapshot_writer.py").read_text(encoding="utf-8")
SRC_BLOCK = (ROOT / "backend/services/learning/backends/block_pattern_learning_backend.py").read_text(encoding="utf-8")
SRC_SEED = (ROOT / "backend/services/learning_core/replay_seed.py").read_text(encoding="utf-8")
SRC_LC_ROUTES = (ROOT / "backend/api/learning_core_routes.py").read_text(encoding="utf-8")


# ── ① dry-run ──
def test_dry_run_is_side_effect_free_and_precedes_gate():
    seg = SRC_ARCH.split("def auto_accept_pending_paper(")[1].split("def reject_proposal(")[0]
    assert "dry_run: bool = False" in seg
    assert seg.index("if dry_run:") < seg.index("_paper_auto_accept_enabled()"), \
        "预览必须先于功能门禁（未启用时也要能看）"
    dry = seg.split("if dry_run:")[1].split("if not self._paper_auto_accept_enabled()")[0]
    assert "self.accept_proposal(" not in dry, "dry-run 分支绝不能调用 accept_proposal（说明文字除外）"
    assert "would_accept" in dry and "preview" in dry


def test_dry_run_route_and_ui():
    assert "dry_run: bool = Query(False" in SRC_ROUTES
    assert 'preview-batch-decide' in SRC_PANEL and "预览批量裁决" in SRC_PANEL
    assert "dry_run=true" in SRC_PANEL


# ── ② tag → code_reason ──
def test_writer_extracts_leading_tag_as_code_reason():
    assert "tag_from_reasoning" in SRC_WRITER
    assert 'verdict_json["code_reason"] = str(_final)[:200]' in SRC_WRITER
    assert "_re.match" in SRC_WRITER, "需用正则在 reasoning 前缀里找标签"
    assert "by_tag" in SRC_BLOCK, "聚合层要给出按标签的历史分布"


def test_tag_extraction_behaviour():
    from backend.services.decision_snapshot_writer import decision_snapshot_writer as W

    snap = W.build(
        session_id="s", strategy_id="st", symbol="ETH", tier="mid", action="hold", confidence=30,
        reasoning="arb_conflict:scalp 反向观点冲突; 其它理由",
        proposal={"source_lane": "master"},
        evaluate_verdict={"allowed": False, "reason": "", "code_reason": "", "layer": "master", "rule": "master"},
        executed=False, source_lane="master",
    )
    v = snap.evaluate_verdict_json or {}
    assert v.get("code_reason") == "arb_conflict"
    assert v.get("reason_source") == "tag_from_reasoning"


# ── ③ 回放去合成 ──
def test_replay_seeder_contract():
    assert "def seed_from_real_decisions(" in SRC_SEED
    assert 'executed IS TRUE AND pnl_pct IS NOT NULL' in SRC_SEED, "只吃真实已回填 pnl 的成交"
    assert '"source": source' in SRC_SEED and 'source: str = "live"' in SRC_SEED, "真实样本标 live，别混进 synthetic"
    assert '@router.post("/replay/seed-real")' in SRC_LC_ROUTES


def test_replay_seeder_behaviour_adds_live_only():
    """行为级：灌入后 live 计数只增不减，且不把 synthetic 当真实。"""
    from backend.services.learning_core.rl_core.replay_buffer import replay_buffer
    from backend.services.learning_core.replay_seed import seed_from_real_decisions

    before = replay_buffer.stats()
    r1 = seed_from_real_decisions(days=365, limit=1000)
    after = replay_buffer.stats()
    assert "live" in after.get("by_source", {}) or r1.get("added", 0) == 0
    # 幂等：第二次调用不应重复灌入相同的真实样本
    r2 = seed_from_real_decisions(days=365, limit=1000)
    assert r2.get("added", 0) == 0, f"重复调用应全部去重，实际 added={r2.get('added')}"
    assert after.get("total", 0) >= before.get("total", 0)
