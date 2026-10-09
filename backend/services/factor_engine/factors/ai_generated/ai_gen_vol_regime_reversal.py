"""AI因子: 波动率突变短期反转 | 置信:55% | 短期已实现波动率相对长期波动率的突变比率，与短期收益方向交互，高波动突变后短期收益易反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityRegimeShortReversal(BaseFactor):
    """短期已实现波动率相对长期波动率的突变比率，与短期收益方向交互，高波动突变后短期收益易反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_regime_reversal",
            name="Volatility Regime Short Reversal",
            display_name="波动率突变短期反转",
            description="短期已实现波动率相对长期波动率的突变比率，与短期收益方向交互，高波动突变后短期收益易反转。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        vol_s = data['close'].pct_change().rolling(5).std()
        vol_l = data['close'].pct_change().rolling(40).std()
        ratio = vol_s / (vol_l + 1e-9)
        ret3 = data['close'].pct_change(3)
        result = ((ratio - 1) * (-ret3)).clip(-1, 1)
        return result
