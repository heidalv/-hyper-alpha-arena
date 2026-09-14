"""AI因子: 放量突破确认 | 置信:58% | 短期动量与成交量放大的交互：当价格突破且成交量显著高于中位数时，动量延续概率更高，构造量价共振因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeBreakoutConfirmation(BaseFactor):
    """短期动量与成交量放大的交互：当价格突破且成交量显著高于中位数时，动量延续概率更高，构造量价共振因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_breakout_confirm",
            name="Volume Breakout Confirmation",
            display_name="放量突破确认",
            description="短期动量与成交量放大的交互：当价格突破且成交量显著高于中位数时，动量延续概率更高，构造量价共振因子。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        mom = data['close'].pct_change(5)
        vol_ratio = data['volume'] / (data['volume'].rolling(50).median() + 1e-9)
        result = (mom * (vol_ratio - 1)).rolling(3).mean().clip(-1, 1)
        return result
