"""AI因子: 量价背离风险 | 置信:55% | 识别价格动量与成交量的背离。计算短期价格收益率（5日）与成交量变化率（5日）的相关系数负值，若两者反向运动（负相关），则预示趋势脆弱，可能反转。输出[-1,1]，负值表示存在背离风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeMomentumDivergence(BaseFactor):
    """识别价格动量与成交量的背离。计算短期价格收益率（5日）与成交量变化率（5日）的相关系数负值，若两者反向运动（负相关），则预示趋势脆弱，可能反转。输出[-1,1]，负值表示存在背离风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_mom_div",
            name="Volume-Momentum Divergence",
            display_name="量价背离风险",
            description="识别价格动量与成交量的背离。计算短期价格收益率（5日）与成交量变化率（5日）的相关系数负值，若两者反向运动（负相关），则预示趋势脆弱，可能反转。输出[-1,1]，负值表示存在背离风险。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        volume = data['volume']
        # Price return (5-day)
        ret = close.pct_change(5)
        # Volume change (5-day ratio)
        vol_chg = volume / volume.shift(5) - 1
        # Rolling correlation between returns and volume change over 20 periods
        corr = ret.rolling(window=20).corr(vol_chg)
        # Divergence: if correlation is negative (price up but volume down etc.), risk
        # We map: corr from -1 to 1 -> factor from -1 to 1, but negative corr gives negative factor
        # Actually we want negative factor when corr negative (divergence)
        result = -corr  # So negative corr -> positive result? No, we want to signal caution (negative factor)
        # So if corr negative, result positive? Wait, we want factor -1 to 1, negative means risk.
        # Let's define factor = -corr, then if corr=-0.5 -> factor=0.5 (positive), not risk.
        # Better: factor = corr, but then positive corr gives positive factor, negative gives negative.
        # Actually divergence means price and volume move opposite (negative corr), which is risky -> negative factor.
        # So factor = corr directly: negative corr => negative factor, good.
        # But also consider magnitude: strong negative corr -> -1 factor.
        result = corr
        # result already in [-1,1], but needs to fill NaN
        result = result.fillna(0)
        return result
