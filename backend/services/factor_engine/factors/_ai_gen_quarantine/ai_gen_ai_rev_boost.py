"""AI因子: AI反转增强 | 置信:55% | 基于短期动量与长期均值偏离的均值回复信号。计算过去5日收益与过去20日平均收益的差值，结合当前价格相对于过去50日均线的位置。当短期动量过度偏离长期均值且价格远离均线时，预测反转。正负值表示反转方向和强度。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class AIReverseBoost(BaseFactor):
    """基于短期动量与长期均值偏离的均值回复信号。计算过去5日收益与过去20日平均收益的差值，结合当前价格相对于过去50日均线的位置。当短期动量过度偏离长期均值且价格远离均线时，预测反转。正负值表示反转方向和强度。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ai_rev_boost",
            name="AI Reverse Boost",
            display_name="AI反转增强",
            description="基于短期动量与长期均值偏离的均值回复信号。计算过去5日收益与过去20日平均收益的差值，结合当前价格相对于过去50日均线的位置。当短期动量过度偏离长期均值且价格远离均线时，预测反转。正负值表示反转方向和强度。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        ret5 = close.pct_change(5)
        ret20_avg = close.pct_change(20).rolling(10).mean()
        # 动量偏离
        momentum_dev = ret5 - ret20_avg
        # 价格相对于均线
        ma50 = close.rolling(50).mean()
        price_dev = (close - ma50) / ma50
        # 综合信号：偏离越大，反转概率越高
        raw = -momentum_dev * price_dev * 100  # 负号使得反向
        # 归一化到[-1,1]使用atan
        result = np.arctan(raw) / (np.pi/2)
        return pd.Series(result, index=data.index).fillna(0)
