"""AI因子: 波动率反转风险 | 置信:60% | 高波动率环境下价格容易剧烈反转，做空易被止损。使用ATR与价格相对于均线的偏离度衡量。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityReversalRisk(BaseFactor):
    """高波动率环境下价格容易剧烈反转，做空易被止损。使用ATR与价格相对于均线的偏离度衡量。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_reversal",
            name="Volatility_Reversal_Risk",
            display_name="波动率反转风险",
            description="高波动率环境下价格容易剧烈反转，做空易被止损。使用ATR与价格相对于均线的偏离度衡量。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算ATR(14)
        tr = np.maximum(data['high'] - data['low'],
                        np.abs(data['high'] - data['close'].shift(1)),
                        np.abs(data['low'] - data['close'].shift(1)))
        atr = tr.rolling(14).mean()
        # 价格相对于20日均线的偏离 (百分比)
        ma20 = data['close'].rolling(20).mean()
        deviation = (data['close'] - ma20) / ma20
        # 高波动率且价格远离均线（超过2个ATR）时，反转概率大
        volatility_high = atr > atr.rolling(50).median() * 1.5
        extreme_dev = np.abs(deviation) > (2 * atr / ma20)
        # 对空头来说，价格高于均线且高波动时做空危险（向上反转）
        bear_risk = (deviation > 0) & volatility_high & extreme_dev
        # 映射为负值表示做空不利
        result = -bear_risk.astype(float)
        # 平滑并归一化
        result = result.rolling(3).mean().fillna(0).clip(-1, 1)
        return result
