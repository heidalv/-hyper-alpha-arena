"""AI因子: 波动率稳定性 | 置信:50% | 衡量近期波动率相对于长期波动率的稳定性，避免波动率突然放大或缩小导致的亏损。使用ATR比率，将短期ATR与长期ATR的比值映射到[-1,1]，比值接近1表示稳定，偏离越远越不稳定。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Stability(BaseFactor):
    """衡量近期波动率相对于长期波动率的稳定性，避免波动率突然放大或缩小导致的亏损。使用ATR比率，将短期ATR与长期ATR的比值映射到[-1,1]，比值接近1表示稳定，偏离越远越不稳定。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volstb",
            name="Volatility Stability",
            display_name="波动率稳定性",
            description="衡量近期波动率相对于长期波动率的稳定性，避免波动率突然放大或缩小导致的亏损。使用ATR比率，将短期ATR与长期ATR的比值映射到[-1,1]，比值接近1表示稳定，偏离越远越不稳定。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        short_window = 5
        long_window = 20
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        short_atr = tr.rolling(short_window).mean()
        long_atr = tr.rolling(long_window).mean()
        # 比值，避免除零
        ratio = short_atr / (long_atr + 1e-10)
        # 理想值为1，偏离越大越不好，映射到[-1,1]
        # 使用log比值，然后压缩
        log_ratio = np.log(ratio + 1e-10)
        # 对数范围大致-3到3，用tanh压缩到-1,1
        result = pd.Series(-np.tanh(log_ratio * 2), index=data.index)  # 负号：稳定为正，不稳定为负
        return result
