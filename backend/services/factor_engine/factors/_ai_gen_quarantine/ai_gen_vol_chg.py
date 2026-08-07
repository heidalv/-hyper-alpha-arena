"""AI因子: 波动率状态变化 | 置信:60% | 衡量短期与长期波动率的变化比率，正值表示波动率上升，负值表示下降。当波动率剧烈变化时，市场处于不稳定状态，容易引发持仓超时或小单亏损。使用ATR比值，输出范围[-1,1]映射。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Regime_Change(BaseFactor):
    """衡量短期与长期波动率的变化比率，正值表示波动率上升，负值表示下降。当波动率剧烈变化时，市场处于不稳定状态，容易引发持仓超时或小单亏损。使用ATR比值，输出范围[-1,1]映射。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_chg",
            name="Volatility Regime Change",
            display_name="波动率状态变化",
            description="衡量短期与长期波动率的变化比率，正值表示波动率上升，负值表示下降。当波动率剧烈变化时，市场处于不稳定状态，容易引发持仓超时或小单亏损。使用ATR比值，输出范围[-1,1]映射。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        period_short = 5
        period_long = 20
        # ATR计算
        high_low = data['high'] - data['low']
        high_close = (data['high'] - data['close'].shift(1)).abs()
        low_close = (data['low'] - data['close'].shift(1)).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr_short = tr.rolling(period_short, min_periods=period_short).mean()
        atr_long = tr.rolling(period_long, min_periods=period_long).mean()
        # 比率，平滑避免除零
        ratio = atr_short / (atr_long + 1e-10)
        # 映射到[-1,1]，使用tanh归一化
        result = (ratio - 1).clip(-1, 1)  # 简单截断，更精细可用np.tanh(ratio-1)*2-1但保持简单
        return result
