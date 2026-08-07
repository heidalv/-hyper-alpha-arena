"""AI因子: 影线失衡因子 | 置信:60% | 利用上下影线长度比例与当前价格在近期高低区间中的位置，衡量多空力量失衡。上影线占比高且价格接近区间高位预示抛压，下影线占比高且价格低位预示买盘。通过计算(上影线比例-下影线比例)乘以价格分位，并映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class WickImbalanceFactor(BaseFactor):
    """利用上下影线长度比例与当前价格在近期高低区间中的位置，衡量多空力量失衡。上影线占比高且价格接近区间高位预示抛压，下影线占比高且价格低位预示买盘。通过计算(上影线比例-下影线比例)乘以价格分位，并映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_wick_ratio",
            name="Wick Imbalance Factor",
            display_name="影线失衡因子",
            description="利用上下影线长度比例与当前价格在近期高低区间中的位置，衡量多空力量失衡。上影线占比高且价格接近区间高位预示抛压，下影线占比高且价格低位预示买盘。通过计算(上影线比例-下影线比例)乘以价格分位，并映射到[-1,1]。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        lookback = 20
        # 影线比例（避免除以零）
        range_ = data['high'] - data['low']
        upper_wick = data['high'] - np.maximum(data['open'], data['close'])
        lower_wick = np.minimum(data['open'], data['close']) - data['low']
        wick_ratio = (upper_wick - lower_wick) / (range_ + 1e-10)
        # 价格在近期高低中的位置
        high_max = data['high'].rolling(lookback).max()
        low_min = data['low'].rolling(lookback).min()
        price_pos = (data['close'] - low_min) / (high_max - low_min + 1e-10)
        # 加权：上影线长且价格在高位则负（看空），下影线长且价格在低位则正（看多）
        signal = wick_ratio * (price_pos - 0.5) * 2  # 将price_pos中心化到-0.5~0.5
        # 使用tanh压缩到[-1,1]
        signal = np.tanh(signal * 2)
        return signal
