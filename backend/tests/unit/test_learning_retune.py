"""[阶段3f] learning_bridge OWM delta 相对化 — 单元测试。

阶段3a 把 llm_qual 基础权重 0.04→0.30 后,旧的绝对 delta(±0.02/±0.03)
对不同源产生 13 倍差异的相对摆动(对小权重源过度调整)。本测试验证:

  A. _base_weight_for_source:OWM 源 → decision_hub 信号基础权重映射
     - llm → llm_qual(0.30,长线)
     - orch → orch_long_bias(0.12,长线)/ orch_mid_bias(0.12,中线)
     - quant → quant_alignment
     - learning → feedback_loop
     - prescreen/regime → 兜底 0.1
  B. delta 比例:±5% 基础权重(非绝对值)
     - llm 赢: +0.30*0.05 = +0.015(旧 +0.02)
     - orch 赢: +0.12*0.05 = +0.006(旧 +0.02,旧逻辑过度奖励)
     - llm 输: -0.015(旧 -0.03)
  C. invalidation 惩罚:-10% 基础权重(非绝对 -0.05)
     - llm invalidation 输: -0.30*(0.05+0.10) = -0.045(旧 -0.08)
  D. OWM 乘子仍 clamp [0.5, 1.5]
  E. 赢/输计数仍正确累加
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

# 确保可 import backend.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from backend.services.mlto import learning_bridge
from backend.services.mlto.learning_bridge import (
    _OWM_DEFAULT_BASE_WEIGHT,
    _OWM_DELTA_PCT,
    _OWM_INVALIDATION_PENALTY_PCT,
    _base_weight_for_source,
    _bump_owm,
)


# ════════════════════════════════════════════════════════════════════
# A. _base_weight_for_source
# ════════════════════════════════════════════════════════════════════
class TestBaseWeightLookup:
    def test_llm_long_maps_to_llm_qual(self):
        """llm → llm_qual(阶段3a 后 = 0.30)。"""
        bw = _base_weight_for_source("llm", "long")
        assert bw == pytest.approx(0.30)

    def test_llm_mid_maps_to_llm_qual(self):
        bw = _base_weight_for_source("llm", "mid")
        assert bw == pytest.approx(0.30)

    def test_orch_long_maps_to_orch_long_bias(self):
        bw = _base_weight_for_source("orch", "long")
        assert bw == pytest.approx(0.12)

    def test_orch_mid_maps_to_orch_mid_bias(self):
        bw = _base_weight_for_source("orch", "mid")
        assert bw == pytest.approx(0.12)

    def test_quant_maps_to_quant_alignment_long(self):
        bw = _base_weight_for_source("quant", "long")
        assert bw == pytest.approx(0.12)

    def test_quant_maps_to_quant_alignment_mid(self):
        bw = _base_weight_for_source("quant", "mid")
        assert bw == pytest.approx(0.18)

    def test_learning_maps_to_feedback_loop_long(self):
        bw = _base_weight_for_source("learning", "long")
        assert bw == pytest.approx(0.03)

    def test_learning_maps_to_feedback_loop_mid(self):
        bw = _base_weight_for_source("learning", "mid")
        assert bw == pytest.approx(0.08)

    def test_prescreen_unknown_uses_default(self):
        """prescreen/regime 无对应信号 → 兜底 0.1。"""
        assert _base_weight_for_source("prescreen", "long") == _OWM_DEFAULT_BASE_WEIGHT
        assert _base_weight_for_source("regime", "mid") == _OWM_DEFAULT_BASE_WEIGHT


# ════════════════════════════════════════════════════════════════════
# B. delta 比例(±5% 基础权重,非绝对值)
# ════════════════════════════════════════════════════════════════════
class TestRelativeDelta:
    def test_constants_match_decision(self):
        """决策4: ±5% delta, 10% invalidation 惩罚。"""
        assert _OWM_DELTA_PCT == 0.05
        assert _OWM_INVALIDATION_PENALTY_PCT == 0.10

    def test_llm_win_delta_is_5pct_of_base_not_absolute_002(self):
        """llm 赢:+0.30*0.05 = +0.015(旧 +0.02)。"""
        bw = _base_weight_for_source("llm", "long")
        assert bw * _OWM_DELTA_PCT == pytest.approx(0.015)
        # 关键:不再是旧的绝对 0.02
        assert abs(bw * _OWM_DELTA_PCT - 0.02) > 1e-9

    def test_orch_win_delta_smaller_than_llm(self):
        """orch(0.12)的 delta < llm(0.30)的 delta——比例而非绝对。"""
        llm_delta = _base_weight_for_source("llm", "long") * _OWM_DELTA_PCT
        orch_delta = _base_weight_for_source("orch", "long") * _OWM_DELTA_PCT
        assert orch_delta < llm_delta
        assert llm_delta == pytest.approx(0.015)
        assert orch_delta == pytest.approx(0.006)

    def test_llm_loss_delta_is_5pct_of_base(self):
        """llm 输:-0.015(旧 -0.03)。"""
        bw = _base_weight_for_source("llm", "long")
        assert -(bw * _OWM_DELTA_PCT) == pytest.approx(-0.015)

    def test_low_weight_source_not_over_adjusted(self):
        """learning/feedback_loop(0.03)在旧绝对 delta 下被过度惩罚。

        旧:+0.02/-0.03 是基础权重 0.03 的 67%/100%——明显过度。
        新:±0.0015 是 5%,合理。
        """
        bw = _base_weight_for_source("learning", "long")
        assert bw == pytest.approx(0.03)
        new_delta = bw * _OWM_DELTA_PCT
        assert new_delta == pytest.approx(0.0015)
        # 新 delta 远小于基础权重(5% vs 旧的 67%)
        assert new_delta / bw == pytest.approx(0.05)


# ════════════════════════════════════════════════════════════════════
# C. invalidation 惩罚(-10% 基础权重)
# ════════════════════════════════════════════════════════════════════
class TestInvalidationPenalty:
    def test_invalidation_loss_penalty_llm(self):
        """llm invalidation 输:-0.30*(0.05+0.10) = -0.045。

        旧:-(0.03+0.05) = -0.08(绝对值)。
        """
        bw = _base_weight_for_source("llm", "long")
        delta = -(bw * _OWM_DELTA_PCT) - (bw * _OWM_INVALIDATION_PENALTY_PCT)
        assert delta == pytest.approx(-0.045)
        # 不再是旧的绝对 -0.08
        assert abs(delta - (-0.08)) > 1e-9

    def test_invalidation_penalty_proportional(self):
        """invalidation 惩罚与基础权重成正比。"""
        llm_penalty = _base_weight_for_source("llm", "long") * _OWM_INVALIDATION_PENALTY_PCT
        orch_penalty = _base_weight_for_source("orch", "long") * _OWM_INVALIDATION_PENALTY_PCT
        assert llm_penalty == pytest.approx(0.03)
        assert orch_penalty == pytest.approx(0.012)
        assert orch_penalty < llm_penalty


# ════════════════════════════════════════════════════════════════════
# D/E. _bump_owm 端到端(mock DB)
# ════════════════════════════════════════════════════════════════════
def _make_mock_db(existing_weight=1.0):
    """构造一个会返回单个 weight row 的 mock adb。

    MltoSignalWeight 模拟对象:weight/win_count/loss_count 可读写。
    """
    row = SimpleNamespace(
        source="llm", tier="long",
        weight=existing_weight,
        win_count=0, loss_count=0,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = row
    return db, row


class TestBumpOwmEndToEnd:
    def test_llm_win_bumps_by_5pct(self):
        db, row = _make_mock_db(existing_weight=1.0)
        _bump_owm(db, "sess", "long", ["evt-1"], 100.0, {}, db)
        assert row.weight == pytest.approx(1.0 + 0.015)
        assert row.win_count == 1
        assert row.loss_count == 0

    def test_llm_loss_reduces_by_5pct(self):
        db, row = _make_mock_db(existing_weight=1.0)
        _bump_owm(db, "sess", "long", ["evt-1"], -50.0, {}, db)
        assert row.weight == pytest.approx(1.0 - 0.015)
        assert row.loss_count == 1
        assert row.win_count == 0

    def test_invalidation_loss_applies_extra_10pct(self):
        """close_reason 含 'invalidation' → 额外 -10%。"""
        db, row = _make_mock_db(existing_weight=1.0)
        _bump_owm(
            db, "sess", "long", ["evt-1"], -50.0,
            {"close_reason": "thesis_invalidation:price_below_level"}, db,
        )
        # delta = -0.015 - 0.03 = -0.045
        assert row.weight == pytest.approx(1.0 - 0.045)

    def test_invalidation_win_still_negative(self):
        """invalidation + 赢:仍是 +5% - 10% = -5%(invalidation 总是惩罚)。"""
        db, row = _make_mock_db(existing_weight=1.0)
        _bump_owm(
            db, "sess", "long", ["evt-1"], 100.0,
            {"close_reason": "thesis_invalidation"}, db,
        )
        # delta = +0.015 - 0.03 = -0.015
        assert row.weight == pytest.approx(1.0 - 0.015)
        # 但 pnl>0 仍记 win
        assert row.win_count == 1

    def test_owm_clamped_at_upper_bound(self):
        """OWM 乘子 clamp [0.5, 1.5]——连赢不会超 1.5。"""
        db, row = _make_mock_db(existing_weight=1.49)
        _bump_owm(db, "sess", "long", ["evt-1"], 100.0, {}, db)
        assert row.weight == pytest.approx(1.5)

    def test_owm_clamped_at_lower_bound(self):
        db, row = _make_mock_db(existing_weight=0.51)
        _bump_owm(db, "sess", "long", ["evt-1"], -50.0, {}, db)
        assert row.weight == pytest.approx(0.5)

    def test_default_source_llm_when_no_cited_ids(self):
        """无 cited_ids → sources 默认 ["llm"],仍按 llm 基础权重算 delta。"""
        db, row = _make_mock_db(existing_weight=1.0)
        _bump_owm(db, "sess", "long", [], 100.0, {}, db)
        assert row.weight == pytest.approx(1.0 + 0.015)
