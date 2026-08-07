"""AI因子: 布林带收缩 | 置信:70% | 检测布林带带宽是否收缩至近期低点。带宽低于过去20日最小值时，市场处于极度窄幅震荡，容易触发止损，输出负值；带宽扩张时输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Bollinger_Band_Contraction(BaseFactor):
    """检测布林带带宽是否收缩至近期低点。带宽低于过去20日最小值时，市场处于极度窄幅震荡，容易触发止损，输出负值；带宽扩张时输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bbcontract",
            name="Bollinger Band Contraction",
            display_name="布林带收缩",
            description="检测布林带带宽是否收缩至近期低点。带宽低于过去20日最小值时，市场处于极度窄幅震荡，容易触发止损，输出负值；带宽扩张时输出正值。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        period = 20
        std = data['close'].rolling(window=period).std()
        ma = data['close'].rolling(window=period).mean()
        upper = ma + 2 * std
        lower = ma - 2 * std
        bandwidth = (upper - lower) / ma
        # 计算过去period日内最小带宽
        min_band = bandwidth.rolling(window=period).min()
        # 当前带宽与最小值比较：接近最小值时信号为负，远离时为正
        # 用相对差值： (bandwidth - min_band) / min_band，然后映射
        relative_diff = (bandwidth - min_band) / (min_band + 1e-10)
        signal = np.tanh(relative_diff * 3)  # 负值表示收缩
        return signal.fillna(0)
