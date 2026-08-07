"""AI因子: 波动率挤压 | 置信:60% | 利用布林带宽度衡量波动率水平。当带宽处于历史低位时，市场进入低波动挤压状态，此时趋势性机会减少，容易导致持仓超时或小幅止损。负值表示低波动挤压风险高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilitySqueezeRisk(BaseFactor):
    """利用布林带宽度衡量波动率水平。当带宽处于历史低位时，市场进入低波动挤压状态，此时趋势性机会减少，容易导致持仓超时或小幅止损。负值表示低波动挤压风险高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vsqueeze",
            name="Volatility Squeeze Risk",
            display_name="波动率挤压",
            description="利用布林带宽度衡量波动率水平。当带宽处于历史低位时，市场进入低波动挤压状态，此时趋势性机会减少，容易导致持仓超时或小幅止损。负值表示低波动挤压风险高。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ma = close.rolling(20, min_periods=10).mean()
        std = close.rolling(20, min_periods=10).std()
        bb_width = (2 * std) / ma
        width_mean = bb_width.rolling(200, min_periods=50).mean()
        width_std = bb_width.rolling(200, min_periods=50).std()
        zscore = (bb_width - width_mean) / width_std
        result = -zscore.clip(-3, 3) / 3.0
        return result.fillna(0)
