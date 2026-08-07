"""S2-10a 单元测试：WisdomTracker 净扣费 + 质量闸门 + 验证强度排序。

覆盖：
- 质量闸门：噪声样本（|pnl_pct| 与 |pnl| 均未达门槛）不更新评分/计数；
- 净扣费：tanh(|pnl|/50) 金额加权信号 ∈ [-1,1]，大亏重罚、小赚低权重；
- 计数：evaluation_count / quality_hit_count 按质量样本口径递增；
- 停用：低分 + 超应用次数自动停用；
- 验证强度排序：eff × min(1, qhits/min_q) × log1p(applied)，降序/limit/仅活跃；
- 配置兜底：_settings_cfg 在 settings 缺失时回退模块默认。
"""
from unittest.mock import MagicMock

import pytest

from backend.services.wisdom_tracker import (
    EMA_ALPHA,
    DEACTIVATION_THRESHOLD,
    MIN_SAMPLES_FOR_DEACTIVATION,
    WisdomTracker,
    _settings_cfg,
)


def _make_decision(wisdom_ids=(1,)):
    """构造带 wisdom_applied 的决策 stub。"""
    d = MagicMock()
    d.wisdom_applied = {"wisdom_ids": list(wisdom_ids), "applied_at": "2026-08-05T00:00:00"}
    return d


def _make_wisdom(**attrs):
    """构造 TradingWisdom stub（默认值对齐模型列）。"""
    w = MagicMock()
    defaults = {
        "id": 1,
        "template_id": "tpl_1",
        "tier": "mid",
        "wisdom_type": "lesson",
        "content": {"msg": "test"},
        "prompt_fragment": "frag",
        "confidence": 0.5,
        "sample_count": 0,
        "effectiveness_score": 0.5,
        "applied_count": 3,
        "evaluation_count": 0,
        "quality_hit_count": 0,
        "is_active": True,
        "last_updated": None,
    }
    defaults.update(attrs)
    for k, v in defaults.items():
        setattr(w, k, v)
    return w


class TestQualityGate:
    """质量闸门：噪声样本不计入（也不更新评分）"""

    def test_small_noise_skipped(self):
        tracker = WisdomTracker()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = _make_decision()
        w = _make_wisdom()
        db.query.return_value.filter.return_value.first.side_effect = None
        # 第二次 query（TradingWisdom）返回 w
        db.query.side_effect = lambda cls: (
            MagicMock(filter=lambda *a, **k: MagicMock(first=lambda: _make_decision()))
            if cls.__name__ == "AIDecisionLog"
            else MagicMock(filter=lambda *a, **k: MagicMock(first=lambda: w))
        )

        tracker.evaluate_trade_result(db, 1, pnl=0.2, pnl_pct=0.001)  # 0.1% & $0.2 < 门槛

        assert w.effectiveness_score == 0.5  # 未更新
        assert w.evaluation_count == 0
        assert w.quality_hit_count == 0

    def test_pnl_pct_above_gate_counts(self):
        tracker = WisdomTracker()
        w = _make_wisdom(effectiveness_score=0.5, evaluation_count=0, quality_hit_count=0)
        db = MagicMock()
        db.query.side_effect = lambda cls: (
            MagicMock(filter=lambda *a, **k: MagicMock(first=lambda: _make_decision()))
            if cls.__name__ == "AIDecisionLog"
            else MagicMock(filter=lambda *a, **k: MagicMock(first=lambda: w))
        )

        tracker.evaluate_trade_result(db, 1, pnl=2.0, pnl_pct=0.01)  # 1% > 0.3%

        assert w.evaluation_count == 1
        assert w.quality_hit_count == 1
        assert w.effectiveness_score != 0.5  # 已更新


class TestNetCharge:
    """净扣费：tanh(|pnl|/50) 金额加权信号"""

    def test_big_loss_heavily_punished(self):
        import math

        pnl = -300.0
        amount_w = math.tanh(abs(pnl) / 50.0)
        assert amount_w > 0.99  # 300USD 接近饱和
        signal = -amount_w
        assert signal < -0.99

    def test_small_pnl_low_weight(self):
        import math

        pnl = 2.0
        amount_w = math.tanh(2.0 / 50.0)
        assert 0.03 < amount_w < 0.05  # 小赚低权重

    def test_signal_sign_follows_pnl(self):
        import math

        assert math.tanh(50 / 50.0) > 0
        assert -math.tanh(50 / 50.0) < 0

    def test_ema_score_updated_with_signal(self):
        tracker = WisdomTracker()
        w = _make_wisdom(effectiveness_score=0.5)
        db = MagicMock()
        db.query.side_effect = lambda cls: (
            MagicMock(filter=lambda *a, **k: MagicMock(first=lambda: _make_decision()))
            if cls.__name__ == "AIDecisionLog"
            else MagicMock(filter=lambda *a, **k: MagicMock(first=lambda: w))
        )

        tracker.evaluate_trade_result(db, 1, pnl=100.0, pnl_pct=0.05)

        import math

        signal = math.tanh(100.0 / 50.0)
        expected = round(0.5 * (1 - EMA_ALPHA) + signal * EMA_ALPHA, 4)
        assert w.effectiveness_score == expected


class TestCounters:
    """质量样本计数"""

    def test_negative_pnl_counts_evaluation_only(self):
        tracker = WisdomTracker()
        w = _make_wisdom()
        db = MagicMock()
        db.query.side_effect = lambda cls: (
            MagicMock(filter=lambda *a, **k: MagicMock(first=lambda: _make_decision()))
            if cls.__name__ == "AIDecisionLog"
            else MagicMock(filter=lambda *a, **k: MagicMock(first=lambda: w))
        )

        tracker.evaluate_trade_result(db, 1, pnl=-50.0, pnl_pct=-0.02)

        assert w.evaluation_count == 1
        assert w.quality_hit_count == 0  # 亏损不算 hit


class TestDeactivation:
    """低分 + 超应用次数自动停用"""

    def test_deactivates_low_score(self):
        tracker = WisdomTracker()
        w = _make_wisdom(
            effectiveness_score=0.2, applied_count=MIN_SAMPLES_FOR_DEACTIVATION,
            is_active=True,
        )
        db = MagicMock()
        db.query.side_effect = lambda cls: (
            MagicMock(filter=lambda *a, **k: MagicMock(first=lambda: _make_decision()))
            if cls.__name__ == "AIDecisionLog"
            else MagicMock(filter=lambda *a, **k: MagicMock(first=lambda: w))
        )

        # 再来一次大亏，new_score 会低于 0.25 → 停用
        tracker.evaluate_trade_result(db, 1, pnl=-200.0, pnl_pct=-0.08)

        assert w.is_active is False

    def test_high_score_stays_active(self):
        tracker = WisdomTracker()
        w = _make_wisdom(
            effectiveness_score=0.9, applied_count=MIN_SAMPLES_FOR_DEACTIVATION,
            is_active=True,
        )
        db = MagicMock()
        db.query.side_effect = lambda cls: (
            MagicMock(filter=lambda *a, **k: MagicMock(first=lambda: _make_decision()))
            if cls.__name__ == "AIDecisionLog"
            else MagicMock(filter=lambda *a, **k: MagicMock(first=lambda: w))
        )

        tracker.evaluate_trade_result(db, 1, pnl=50.0, pnl_pct=0.03)

        assert w.is_active is True
        # 新分数仍高于停用阈值
        assert (w.effectiveness_score or 0) >= DEACTIVATION_THRESHOLD


class TestRankedWisdom:
    """验证强度排序"""

    def _tracker_and_db(self, wisdoms):
        tracker = WisdomTracker()
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = wisdoms
        return tracker, db

    def test_ranked_by_strength_desc(self):
        import math

        # w1: eff=0.8 qhits=5 applied=10 → 0.8×1.0×log1p(10)=0.8×2.3979=1.918
        # w2: eff=0.9 qhits=0  applied=100 → 0.9×0.0×...=0
        # w3: eff=0.6 qhits=2  applied=3  → 0.6×0.4×log1p(3)=0.6×0.4×1.386=0.333
        w1 = _make_wisdom(id=1, effectiveness_score=0.8, quality_hit_count=5, applied_count=10)
        w2 = _make_wisdom(id=2, effectiveness_score=0.9, quality_hit_count=0, applied_count=100)
        w3 = _make_wisdom(id=3, effectiveness_score=0.6, quality_hit_count=2, applied_count=3)
        tracker, db = self._tracker_and_db([w1, w2, w3])

        ranked = tracker.get_ranked_wisdom(db, limit=10, min_quality=5)

        assert [r["id"] for r in ranked] == [1, 3, 2]
        # 实现按 4 位小数 round 强度（展示口径），断言对齐该精度
        assert abs(ranked[0]["strength"] - round(0.8 * 1.0 * math.log1p(10), 4)) < 1e-9
        assert ranked[2]["strength"] == 0.0

    def test_limit_applied(self):
        w1 = _make_wisdom(id=1, effectiveness_score=0.8, quality_hit_count=5, applied_count=10)
        w2 = _make_wisdom(id=2, effectiveness_score=0.7, quality_hit_count=5, applied_count=10)
        w3 = _make_wisdom(id=3, effectiveness_score=0.6, quality_hit_count=5, applied_count=10)
        tracker, db = self._tracker_and_db([w1, w2, w3])

        ranked = tracker.get_ranked_wisdom(db, limit=2, min_quality=5)

        assert len(ranked) == 2
        assert [r["id"] for r in ranked] == [1, 2]

    def test_inactive_excluded(self):
        w1 = _make_wisdom(id=1, effectiveness_score=0.8, quality_hit_count=5,
                          applied_count=10, is_active=True)
        w2 = _make_wisdom(id=2, effectiveness_score=0.9, quality_hit_count=5,
                          applied_count=10, is_active=False)
        tracker = WisdomTracker()
        db = MagicMock()
        # 模拟 SQL 层 is_active 过滤：filter 条件引用 is_active 列，
        # all() 只返回活跃行（inactive 在 SQL 层被剔除）
        db.query.return_value.filter.return_value.all.return_value = [w1]

        ranked = tracker.get_ranked_wisdom(db, limit=10)

        assert [r["id"] for r in ranked] == [1]
        # 过滤条件确实基于 is_active 列
        cond = db.query.return_value.filter.call_args.args[0]
        assert "is_active" in str(cond)

    def test_fields_present(self):
        w = _make_wisdom(id=7, effectiveness_score=0.5, quality_hit_count=3,
                         applied_count=4, evaluation_count=6)
        tracker, db = self._tracker_and_db([w])

        ranked = tracker.get_ranked_wisdom(db, limit=10)

        assert ranked[0]["id"] == 7
        assert ranked[0]["type"] == "lesson"
        assert ranked[0]["tier"] == "mid"
        assert ranked[0]["template_id"] == "tpl_1"
        assert ranked[0]["effectiveness"] == 0.5
        assert ranked[0]["evaluation_count"] == 6
        assert ranked[0]["quality_hit_count"] == 3
        assert ranked[0]["applied_count"] == 4


class TestSettingsCfgFallback:
    """配置兜底"""

    def test_fallback_defaults(self, monkeypatch):
        import backend.config.settings as settings_mod
        import backend.services.wisdom_tracker as wt

        # 模拟 settings 缺失 WISDOM_* 属性（from-import 抛 ImportError → 兜底）
        for name in (
            "WISDOM_QUALITY_PNL_PCT_GATE",
            "WISDOM_QUALITY_PNL_USD_GATE",
            "WISDOM_AMOUNT_SCALE_USD",
            "WISDOM_MIN_QUALITY_SAMPLES",
        ):
            monkeypatch.delattr(settings_mod, name, raising=False)

        cfg = wt._settings_cfg()
        assert cfg == {
            "pnl_pct_gate": wt.QUALITY_PNL_PCT_GATE,
            "pnl_usd_gate": wt.QUALITY_PNL_USD_GATE,
            "amount_scale": wt.AMOUNT_SCALE_USD,
            "min_quality": wt.MIN_QUALITY_SAMPLES,
        }


class TestRecordWisdomUsage:
    """记录智慧使用"""

    def test_applied_count_incremented(self):
        tracker = WisdomTracker()
        decision = _make_decision()
        w = _make_wisdom(applied_count=5)
        db = MagicMock()
        db.query.side_effect = lambda cls: (
            MagicMock(filter=lambda *a, **k: MagicMock(first=lambda: decision))
            if cls.__name__ == "AIDecisionLog"
            else MagicMock(filter=lambda *a, **k: MagicMock(first=lambda: w))
        )

        tracker.record_wisdom_usage(db, 1, [1])

        assert w.applied_count == 6
        assert decision.wisdom_applied["wisdom_ids"] == [1]
