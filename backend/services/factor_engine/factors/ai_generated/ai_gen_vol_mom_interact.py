"""AI因子: 波动率动量交互 | 置信:58% | 结合波动率突变与多周期动量：用短期已实现波动率相对长期波动率的比值作为环境权重，与5日动量方向相乘。高波动放大动量信号，低波动抑制噪声，从而在不同波动环境下捕捉动量持续性 alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityMomentumInteraction(BaseFactor):
    """结合波动率突变与多周期动量：用短期已实现波动率相对长期波动率的比值作为环境权重，与5日动量方向相乘。高波动放大动量信号，低波动抑制噪声，从而在不同波动环境下捕捉动量持续性 alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_mom_interact",
            name="Volatility Momentum Interaction",
            display_name="波动率动量交互",
            description="结合波动率突变与多周期动量：用短期已实现波动率相对长期波动率的比值作为环境权重，与5日动量方向相乘。高波动放大动量信号，低波动抑制噪声，从而在不同波动环境下捕捉动量持续性 alpha。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change()
        vol_s = ret.rolling(5).std()
        vol_l = ret.rolling(30).std() + 1e-9
        ratio = vol_s / vol_l
        mom = data['close'].pct_change(5)
        result = (mom * ratio).rolling(3).mean().clip(-1, 1)
        return result
