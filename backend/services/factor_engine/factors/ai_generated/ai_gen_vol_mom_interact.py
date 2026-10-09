"""AI因子: 波动率动量交互 | 置信:60% | 多周期动量差(5日减20日收益)除以已实现波动率，衡量风险调整后的动量加速度；高值表示近期动量相对波动更强，预示趋势延续。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityMomentumInteraction(BaseFactor):
    """多周期动量差(5日减20日收益)除以已实现波动率，衡量风险调整后的动量加速度；高值表示近期动量相对波动更强，预示趋势延续。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_mom_interact",
            name="Volatility Momentum Interaction",
            display_name="波动率动量交互",
            description="多周期动量差(5日减20日收益)除以已实现波动率，衡量风险调整后的动量加速度；高值表示近期动量相对波动更强，预示趋势延续。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        mom = data['close'].pct_change(5) - data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = (mom / vol).clip(-1, 1)
        return result
