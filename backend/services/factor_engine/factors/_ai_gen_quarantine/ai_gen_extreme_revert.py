"""AI因子: 极端均值回归因子 | 置信:60% | 计算价格相对于过去20日均线的偏离度，并用ATR归一化。当偏离超过2倍ATR时，发出回归信号：正偏离（超买）为负，负偏离（超卖）为正。该因子识别价格极端后的反转风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Extreme_Mean_Reversion(BaseFactor):
    """计算价格相对于过去20日均线的偏离度，并用ATR归一化。当偏离超过2倍ATR时，发出回归信号：正偏离（超买）为负，负偏离（超卖）为正。该因子识别价格极端后的反转风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_extreme_revert",
            name="Extreme Mean Reversion",
            display_name="极端均值回归因子",
            description="计算价格相对于过去20日均线的偏离度，并用ATR归一化。当偏离超过2倍ATR时，发出回归信号：正偏离（超买）为负，负偏离（超卖）为正。该因子识别价格极端后的反转风险。",
            category="mean_reversion",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ma20 = close.rolling(20).mean()
        tr = pd.concat([data['high'] - data['low'], abs(data['high'] - data['close'].shift(1)), abs(data['low'] - data['close'].shift(1))], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        deviation = (close - ma20) / atr
        result = pd.Series(np.where(deviation > 2, -1.0, np.where(deviation < -2, 1.0, 0.0)), index=data.index)
        return result
