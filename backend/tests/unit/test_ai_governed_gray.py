# -*- coding: utf-8 -*-
"""
test_ai_governed_gray — v6 阶段 2（S2-6）灰度阶梯单元测试

覆盖:
1. resolve_governed_weight：0.60 档校准门禁（未确认回退 0.40 / 确认放行 / clip）
2. 模块级默认行为：无 env 时 0.40 档（不破坏 standard 回归）
3. snapshot_with_hub_mode：决策快照注入 hub_mode/ai_governed_weight
4. extract_hub_mode：str/dict/坏 JSON 容错
5. collect_gray_metrics：按模式分组聚合（决策量/命中率/盈亏比）
6. judge_deterioration：劣化判定（deteriorated / ok / insufficient）
7. gray_verdict 一步式

运行：.venv\\Scripts\\python.exe -m pytest backend\\tests\\unit\\test_ai_governed_gray.py -q
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import patch

from backend.services.mlto import decision_hub as dh


# ═══════════════════════════════════════════════════════════════════
# 1. 0.60 档校准门禁
# ═══════════════════════════════════════════════════════════════════

class TestWeightTierGate:
    def test_0_40_kept_without_confirmation(self):
        """0.40 档无需确认（可先于校准启动）。"""
        assert dh.resolve_governed_weight(0.40, False) == 0.40
        assert dh.resolve_governed_weight(0.40, True) == 0.40

    def test_0_60_falls_back_without_confirmation(self):
        """0.60 档未确认 → 回退 0.40。"""
        assert dh.resolve_governed_weight(0.60, False) == 0.40

    def test_0_60_allowed_with_confirmation(self):
        """0.60 档显式确认（校准完成）→ 放行。"""
        assert dh.resolve_governed_weight(0.60, True) == 0.60

    def test_1_0_gate(self):
        """1.0 档同样受门禁约束。"""
        assert dh.resolve_governed_weight(1.0, False) == 0.40
        assert dh.resolve_governed_weight(1.0, True) == 1.0

    def test_clip_bounds(self):
        """越界配置 clip 到 [0.40, 1.0]。"""
        assert dh.resolve_governed_weight(0.1, True) == 0.40
        assert dh.resolve_governed_weight(2.5, True) == 1.0

    def test_module_default_is_0_40(self):
        """无 env 配置时模块级档位 = 0.40（standard 回归不受影响）。"""
        assert dh._AI_GOVERNED_WEIGHT == 0.40
        assert dh._AI_GOVERNED is False


# ═══════════════════════════════════════════════════════════════════
# 2. 决策快照注入
# ═══════════════════════════════════════════════════════════════════

class TestSnapshotInjection:
    def test_injects_governed_mode(self, monkeypatch):
        from backend.services.mlto.ai_governed_compare import snapshot_with_hub_mode
        monkeypatch.setattr(dh, "_AI_GOVERNED", True)
        monkeypatch.setattr(dh, "_AI_GOVERNED_WEIGHT", 0.40)
        out = snapshot_with_hub_mode({"confidence": 0.7})
        assert out["hub_mode"] == "ai_governed"
        assert out["ai_governed_weight"] == 0.40
        assert out["confidence"] == 0.7

    def test_injects_standard_mode(self):
        from backend.services.mlto.ai_governed_compare import snapshot_with_hub_mode
        out = snapshot_with_hub_mode({"confidence": 0.5})
        assert out["hub_mode"] == "standard"
        assert out["ai_governed_weight"] is None

    def test_failure_keeps_snapshot_unchanged(self):
        from backend.services.mlto.ai_governed_compare import snapshot_with_hub_mode
        with patch("backend.services.mlto.decision_hub.ai_governed_enabled",
                   side_effect=RuntimeError("boom")):
            out = snapshot_with_hub_mode({"a": 1})
        assert out == {"a": 1}


# ═══════════════════════════════════════════════════════════════════
# 3. hub_mode 提取
# ═══════════════════════════════════════════════════════════════════

class TestExtractMode:
    def test_from_dict(self):
        from backend.services.mlto.ai_governed_compare import extract_hub_mode
        assert extract_hub_mode({"hub_mode": "ai_governed"}) == "ai_governed"
        assert extract_hub_mode({"hub_mode": "standard"}) == "standard"

    def test_from_json_str(self):
        from backend.services.mlto.ai_governed_compare import extract_hub_mode
        snap = json.dumps({"hub_mode": "ai_governed", "confidence": 0.8})
        assert extract_hub_mode(snap) == "ai_governed"

    def test_bad_input_defaults_standard(self):
        from backend.services.mlto.ai_governed_compare import extract_hub_mode
        assert extract_hub_mode(None) == "standard"
        assert extract_hub_mode("not-json{") == "standard"
        assert extract_hub_mode({"hub_mode": "weird"}) == "standard"
        assert extract_hub_mode({}) == "standard"


# ═══════════════════════════════════════════════════════════════════
# 4. 灰度指标聚合 + 劣化判定（内存 SQLite Analytics 表）
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture()
def analytics_db():
    engine = create_engine("sqlite://")
    from backend.database.models import AnalyticsBase
    AnalyticsBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()
    engine.dispose()


def _add_log(db, *, mode, operation="buy", pnl=None, executed="false",
             confidence=0.6, age_hours=1):
    from backend.database.models import AIDecisionLog
    snap = json.dumps({
        "hub_mode": mode, "confidence": confidence,
        "ai_governed_weight": 0.40 if mode == "ai_governed" else None,
    })
    db.add(AIDecisionLog(
        account_id=1, reason="t", operation=operation,
        target_portion=0, total_balance=10000, executed=executed,
        decision_snapshot=snap, realized_pnl=pnl,
        decision_time=datetime.now(timezone.utc) - timedelta(hours=age_hours),
    ))


class TestCollectAndJudge:
    def test_grouping_and_metrics(self, analytics_db):
        from backend.services.mlto.ai_governed_compare import collect_gray_metrics
        # standard: 6 win / 4 loss → win_rate 0.6
        for i in range(10):
            _add_log(analytics_db, mode="standard",
                     pnl=10.0 if i < 6 else -5.0, executed="true")
        # governed: 3 win / 7 loss → win_rate 0.3
        for i in range(10):
            _add_log(analytics_db, mode="ai_governed",
                     pnl=10.0 if i < 3 else -5.0, executed="true")
        # 1 个 hold（无 pnl）进 decisions 不进 realized
        _add_log(analytics_db, mode="standard", operation="hold", pnl=None)
        metrics = collect_gray_metrics(analytics_db, days=7)
        std = metrics["standard"]
        gov = metrics["ai_governed"]
        assert std["decisions"] == 11
        assert std["opens"] == 10
        assert std["executed"] == 10
        assert std["realized_count"] == 10
        assert std["win_rate"] == pytest.approx(0.6)
        assert gov["win_rate"] == pytest.approx(0.3)
        # 盈亏比 = 总盈利/总亏损（round 4 位）：std 60/20=3.0；gov 30/35≈0.8571
        assert std["profit_factor"] == pytest.approx(3.0)
        assert gov["profit_factor"] == pytest.approx(0.8571, abs=1e-3)
        assert std["mean_confidence"] == pytest.approx(0.6, abs=1e-4)

    def test_deterioration_detected(self, analytics_db):
        from backend.services.mlto.ai_governed_compare import (
            collect_gray_metrics, judge_deterioration,
        )
        # 各 20 条 realized：std 0.6 vs gov 0.3 → 差 0.3 > 0.05 → deteriorated
        for i in range(20):
            _add_log(analytics_db, mode="standard",
                     pnl=10.0 if i < 12 else -5.0)
        for i in range(20):
            _add_log(analytics_db, mode="ai_governed",
                     pnl=10.0 if i < 6 else -5.0)
        verdict = judge_deterioration(collect_gray_metrics(analytics_db, days=7))
        assert verdict["status"] == "deteriorated"
        assert any("命中率劣化" in r for r in verdict["reasons"])

    def test_ok_when_no_degradation(self, analytics_db):
        from backend.services.mlto.ai_governed_compare import (
            collect_gray_metrics, judge_deterioration,
        )
        # 各 20 条 realized，胜率同为 0.5 → ok
        for i in range(20):
            _add_log(analytics_db, mode="standard",
                     pnl=10.0 if i < 10 else -5.0)
        for i in range(20):
            _add_log(analytics_db, mode="ai_governed",
                     pnl=10.0 if i < 10 else -5.0)
        verdict = judge_deterioration(collect_gray_metrics(analytics_db, days=7))
        assert verdict["status"] == "ok"

    def test_insufficient_samples(self, analytics_db):
        from backend.services.mlto.ai_governed_compare import (
            collect_gray_metrics, judge_deterioration,
        )
        for i in range(5):
            _add_log(analytics_db, mode="ai_governed",
                     pnl=10.0 if i < 1 else -5.0)
        verdict = judge_deterioration(collect_gray_metrics(analytics_db, days=7))
        assert verdict["status"] == "insufficient"
        assert verdict["governed_realized"] == 5

    def test_gray_verdict_aggregates(self, analytics_db):
        from backend.services.mlto.ai_governed_compare import gray_verdict
        for i in range(10):
            _add_log(analytics_db, mode="standard",
                     pnl=10.0 if i < 6 else -5.0)
        out = gray_verdict(analytics_db, days=7)
        assert out["metrics"]["standard"]["decisions"] == 10
        assert out["verdict"]["status"] == "insufficient"  # governed 无样本
