"""AI因子: 波动率状态动量交互 | 置信:60% | 以短期已实现波动率相对长期波动率的分位判断波动状态，在低波动向高波动切换时动量延续性更强。将中期动量按波动率状态加权，高波动放大动量信号，低波动压缩噪声。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityRegimeMomentumInteraction(BaseFactor):
    """以短期已实现波动率相对长期波动率的分位判断波动状态，在低波动向高波动切换时动量延续性更强。将中期动量按波动率状态加权，高波动放大动量信号，低波动压缩噪声。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_regime_momentum",
            name="Volatility Regime Momentum Interaction",
            display_name="波动率状态动量交互",
            description="以短期已实现波动率相对长期波动率的分位判断波动状态，在低波动向高波动切换时动量延续性更强。将中期动量按波动率状态加权，高波动放大动量信号，低波动压缩噪声。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change()
        vol_s = ret.rolling(5).std()
        vol_l = ret.rolling(40).std() + 1e-9
        regime = (vol_s / vol_l).clip(0, 3)
        mom = data['close'].pct_change(10)
        result = (mom * regime).rolling(3).mean().clip(-1, 1)
        return result
