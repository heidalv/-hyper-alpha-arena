"""AI因子: 波动率状态动量 | 置信:58% | 利用短期已实现波动率与长期波动率的比值刻画波动率突变，并与中期动量方向交互。低波动向高波动切换时动量延续性更强，高波动环境则动量衰减，从而预测未来收益方向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityRegimeMomentum(BaseFactor):
    """利用短期已实现波动率与长期波动率的比值刻画波动率突变，并与中期动量方向交互。低波动向高波动切换时动量延续性更强，高波动环境则动量衰减，从而预测未来收益方向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_regime_momentum",
            name="Volatility Regime Momentum",
            display_name="波动率状态动量",
            description="利用短期已实现波动率与长期波动率的比值刻画波动率突变，并与中期动量方向交互。低波动向高波动切换时动量延续性更强，高波动环境则动量衰减，从而预测未来收益方向。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change()
        vol_short = ret.rolling(5).std()
        vol_long = ret.rolling(30).std() + 1e-9
        vol_ratio = (vol_short / vol_long).clip(0, 3)
        mom = data['close'].pct_change(10)
        result = (mom * (vol_ratio - 1)).clip(-1, 1)
        return result
