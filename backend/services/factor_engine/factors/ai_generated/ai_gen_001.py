"""AI因子: 影线不对称动量交互 | 置信:60% | 利用上下影线不对称度与短期动量方向交互，捕捉插针后的延续或反转效应。当下影线主导且短期上涨时，表明买方承接有力，未来继续上涨概率高；当上影线主导且短期下跌时，表明卖方压制持续，未来继续下跌概率高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class WickAsymmetryMomentum(BaseFactor):
    """利用上下影线不对称度与短期动量方向交互，捕捉插针后的延续或反转效应。当下影线主导且短期上涨时，表明买方承接有力，未来继续上涨概率高；当上影线主导且短期下跌时，表明卖方压制持续，未来继续下跌概率高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_001",
            name="Wick_Asymmetry_Momentum",
            display_name="影线不对称动量交互",
            description="利用上下影线不对称度与短期动量方向交互，捕捉插针后的延续或反转效应。当下影线主导且短期上涨时，表明买方承接有力，未来继续上涨概率高；当上影线主导且短期下跌时，表明卖方压制持续，未来继续下跌概率高。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        asym = (lower - upper) / body
        asym_smooth = asym.rolling(3).mean()
        mom = data['close'].pct_change(5)
        result = (asym_smooth * mom).clip(-1, 1)
        return result
