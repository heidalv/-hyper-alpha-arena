"""AI因子: 布林带波动均值回复 | 置信:60% | 基于布林带宽度收缩后的价格突破方向。计算布林带宽度（上轨-下轨）/中轨，当带宽处于近期低位时，价格突破下轨给出负值（看空），突破上轨给出正值（看多）。使用过去20周期最低带宽作为阈值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class BollingerVolatilityMeanReversion(BaseFactor):
    """基于布林带宽度收缩后的价格突破方向。计算布林带宽度（上轨-下轨）/中轨，当带宽处于近期低位时，价格突破下轨给出负值（看空），突破上轨给出正值（看多）。使用过去20周期最低带宽作为阈值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bvm",
            name="Bollinger Volatility Mean Reversion",
            display_name="布林带波动均值回复",
            description="基于布林带宽度收缩后的价格突破方向。计算布林带宽度（上轨-下轨）/中轨，当带宽处于近期低位时，价格突破下轨给出负值（看空），突破上轨给出正值（看多）。使用过去20周期最低带宽作为阈值。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper = sma20 + 2 * std20
        lower = sma20 - 2 * std20
        bandwidth = (upper - lower) / sma20
        min_bw = bandwidth.rolling(40).min()  # 过去40周期最小带宽
        # 当前带宽接近最小带宽（收缩）且价格突破下轨 -> -1；突破上轨 -> 1
        cond_shrink = bandwidth < min_bw * 1.1  # 比最小带宽多10%以内
        signal = pd.Series(0.0, index=close.index)
        signal[(close < lower) & cond_shrink] = -1.0
        signal[(close > upper) & cond_shrink] = 1.0
        return signal
