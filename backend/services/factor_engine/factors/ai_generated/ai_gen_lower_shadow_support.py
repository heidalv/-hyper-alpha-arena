"""AI因子: 长下影线承接反弹 | 置信:55% | 长下影线代表下方买盘承接，锤子线形态后短期有反弹倾向。用下影线占振幅比例衡量承接强度，正向作为未来收益方向预测。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LowerShadowSupportBounce(BaseFactor):
    """长下影线代表下方买盘承接，锤子线形态后短期有反弹倾向。用下影线占振幅比例衡量承接强度，正向作为未来收益方向预测。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_lower_shadow_support",
            name="Lower Shadow Support Bounce",
            display_name="长下影线承接反弹",
            description="长下影线代表下方买盘承接，锤子线形态后短期有反弹倾向。用下影线占振幅比例衡量承接强度，正向作为未来收益方向预测。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        rng = (data['high'] - data['low']) + 1e-9
        lower = data[['open','close']].min(axis=1) - data['low']
        ratio = lower / rng
        result = ratio.rolling(5).mean().clip(-1, 1)
        return result
