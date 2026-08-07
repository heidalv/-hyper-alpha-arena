"""AI因子: 市场状态不确定性 | 置信:60% | 通过比较近期波动率(ATR)与长期波动率的变化，结合价格动量，识别市场是否处于不确定状态。低波动率变化且方向不明则输出接近0，波动率放大且趋势明确则输出强信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeUncertainty(BaseFactor):
    """通过比较近期波动率(ATR)与长期波动率的变化，结合价格动量，识别市场是否处于不确定状态。低波动率变化且方向不明则输出接近0，波动率放大且趋势明确则输出强信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ru",
            name="regime_uncertainty",
            display_name="市场状态不确定性",
            description="通过比较近期波动率(ATR)与长期波动率的变化，结合价格动量，识别市场是否处于不确定状态。低波动率变化且方向不明则输出接近0，波动率放大且趋势明确则输出强信号。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high, low, close = data['high'], data['low'], data['close']
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr_short = tr.rolling(10).mean()
        atr_long = tr.rolling(50).mean()
        # 波动率变化比
        vol_ratio = atr_short / (atr_long + 1e-10)
        # 价格动量（短期）
        mom = (close - close.shift(5)) / close.shift(5)
        # 不确定性信号：当vol_ratio接近1时，趋势不明；远离1时趋势明确
        uncertainty = 1 - np.exp(-(vol_ratio - 1)**2 * 10)  # 接近1时uncertainty接近0，远离1时接近1
        # 方向由动量决定
        direction = np.sign(mom)
        # 结合：强动量且高波动率变化输出强方向信号，否则接近0
        raw = direction * (1 - uncertainty) * vol_ratio.clip(0, 3)
        # 归一化
        std_raw = raw.rolling(20).std()
        result = raw / (std_raw + 1e-10)
        result = result.clip(-1, 1)
        return result.fillna(0.0)
