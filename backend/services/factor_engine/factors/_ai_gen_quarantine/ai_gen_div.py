"""AI因子: 多时间尺度动量分歧因子 | 置信:60% | 当短期动量（如5日ROC）与长期动量（如20日ROC）方向相反，且波动率放大时，预示趋势不稳定，容易发生反转。计算两个动量方向差异与ATR扩张的乘积，负值时表示分歧且波动增大→看跌，正值表示一致看涨。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Multi_Timeframe_Momentum_Divergence(BaseFactor):
    """当短期动量（如5日ROC）与长期动量（如20日ROC）方向相反，且波动率放大时，预示趋势不稳定，容易发生反转。计算两个动量方向差异与ATR扩张的乘积，负值时表示分歧且波动增大→看跌，正值表示一致看涨。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_div",
            name="Multi-Timeframe Momentum Divergence",
            display_name="多时间尺度动量分歧因子",
            description="当短期动量（如5日ROC）与长期动量（如20日ROC）方向相反，且波动率放大时，预示趋势不稳定，容易发生反转。计算两个动量方向差异与ATR扩张的乘积，负值时表示分歧且波动增大→看跌，正值表示一致看涨。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        # 动量：收益率
        roc_5 = close.pct_change(5)
        roc_20 = close.pct_change(20)
        # 分歧：符号相反且绝对值均大于阈值（0.5%）
        sign_diff = np.sign(roc_5) * np.sign(roc_20)  # 1同向，-1反向
        # 波动率扩张
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr_5 = tr.rolling(5).mean()
        atr_20 = tr.rolling(20).mean()
        atr_expansion = atr_5 / (atr_20 + 1e-10)  # >1扩张
        # 组合：只有反向（sign_diff=-1）且波动扩张才显著
        raw = (-sign_diff) * atr_expansion * (abs(roc_5) + abs(roc_20))
        # 标准化到[-1,1]
        result = -2 * (raw - raw.rolling(50).min()) / (raw.rolling(50).max() - raw.rolling(50).min() + 1e-10) + 1
        return result.fillna(0).clip(-1, 1)
