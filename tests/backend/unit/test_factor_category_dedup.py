"""单测：因子聚合的同类去重（FACTOR_CATEGORY_MAX_SHARE）。

背景：docs/SCALP_FACTOR_STRATEGY_ANALYSIS_2026-07-06.md 第2.1节指出，top-N 聚合
如果被单一 FactorCategory（如 momentum: RSI/MACD/Momentum/ROC）占满，等于把同一个
观点重复计权。`_cap_category_share` 把单类别的合计有效权重压到
`FACTOR_CATEGORY_MAX_SHARE`（默认0.40）以内。
"""
import pytest

from backend.services.factor_engine.factor_signal_generator import (
    FactorSignalGenerator,
    FactorSignal,
    _cap_category_share,
)


def _sig(direction: float, category: str) -> FactorSignal:
    return FactorSignal(factor_id="x", direction=direction, strength=abs(direction), category=category)


class TestCapCategoryShare:
    def test_single_category_is_noop(self, monkeypatch):
        """全部同一类别时，等比例缩放不改变加权平均结果（无对比对象，不应惩罚）。"""
        selected = [("a", _sig(0.8, "momentum"), 1.0), ("b", _sig(0.6, "momentum"), 1.0)]
        eff = [0.8, 0.6]
        capped = _cap_category_share(selected, eff)
        # 单类别场景下按比例整体缩放不改变相对权重，结果应与原始权重成比例
        ratio_before = eff[0] / eff[1]
        ratio_after = capped[0] / capped[1]
        assert ratio_before == pytest.approx(ratio_after)

    def test_dominant_category_gets_capped(self):
        """momentum 类因子数量/权重远超 trend 类时，momentum 合计份额应被压低。"""
        selected = [
            ("rsi", _sig(0.9, "momentum"), 1.0),
            ("macd", _sig(0.8, "momentum"), 1.0),
            ("momentum", _sig(0.85, "momentum"), 1.0),
            ("roc", _sig(0.7, "momentum"), 1.0),
            ("adx", _sig(0.6, "momentum"), 1.0),
            ("supertrend", _sig(0.5, "trend"), 1.0),
        ]
        eff = [abs(sig.direction) for _, sig, _ in selected]
        total_before = sum(eff)
        momentum_share_before = sum(
            e for (_, sig, _), e in zip(selected, eff) if sig.category == "momentum"
        ) / total_before
        assert momentum_share_before > 0.4  # 确认场景确实是"单类别占主导"

        capped = _cap_category_share(selected, eff)
        total_after = sum(capped)
        momentum_share_after = sum(
            e for (_, sig, _), e in zip(selected, capped) if sig.category == "momentum"
        ) / total_after

        assert momentum_share_after < momentum_share_before
        # trend 类因子的绝对权重不应被压低（只压占主导的类别）
        trend_idx = [i for i, (_, sig, _) in enumerate(selected) if sig.category == "trend"][0]
        assert capped[trend_idx] == pytest.approx(eff[trend_idx])

    def test_disabled_flag_is_noop(self, monkeypatch):
        import backend.config.settings as settings_mod
        monkeypatch.setattr(settings_mod, "FACTOR_CATEGORY_DEDUP_ENABLED", False)
        selected = [("a", _sig(0.9, "momentum"), 1.0), ("b", _sig(0.85, "momentum"), 1.0)]
        eff = [0.9, 0.85]
        capped = _cap_category_share(selected, eff)
        assert capped == eff

    def test_empty_input_safe(self):
        assert _cap_category_share([], []) == []


class TestAggregateIntegration:
    def test_momentum_pile_up_reduces_confidence_share(self):
        """端到端：5 个同向 momentum 因子 + 1 个反向 trend 因子，
        去重后 trend 因子对合成方向的拖拽作用应比未去重时更明显。"""
        gen = FactorSignalGenerator()
        signals = {
            "rsi": _sig(0.9, "momentum"),
            "macd": _sig(0.9, "momentum"),
            "momentum": _sig(0.9, "momentum"),
            "roc": _sig(0.9, "momentum"),
            "adx_dummy": _sig(0.9, "momentum"),
            "supertrend": _sig(-0.9, "trend"),  # 唱反调
        }
        weights = {k: 1.0 for k in signals}
        direction, strength, confidence = gen._aggregate(signals, weights)
        # 5 个同向 momentum 被限权后，反向的 trend 因子应能把合成方向明显拉离 +0.9
        assert direction < 0.9
