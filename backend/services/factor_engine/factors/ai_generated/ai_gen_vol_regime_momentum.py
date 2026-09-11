"""AI因子: 波动率状态动量 | 置信:55% | 低波动环境下的动量更可持续，高波动环境下的动量易反转。用短期已实现波动率相对长期波动率的分位作为环境调节，与中期动量交互，得到条件动量 alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityRegimeMomentum(BaseFactor):
    """低波动环境下的动量更可持续，高波动环境下的动量易反转。用短期已实现波动率相对长期波动率的分位作为环境调节，与中期动量交互，得到条件动量 alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_regime_momentum",
            name="Volatility Regime Momentum",
            display_name="波动率状态动量",
            description="低波动环境下的动量更可持续，高波动环境下的动量易反转。用短期已实现波动率相对长期波动率的分位作为环境调节，与中期动量交互，得到条件动量 alpha。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change()
        vol_s = ret.rolling(5).std()
        vol_l = ret.rolling(30).std() + 1e-9
        vol_ratio = (vol_s / vol_l).clip(0, 3)
        mom = data['close'].pct_change(15)
        result = (mom * (1.5 - vol_ratio)).clip(-1, 1)
        return result
