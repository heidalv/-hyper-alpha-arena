"""AI因子: 止损规避因子 | 置信:60% | 评估当前价格是否容易触发止损。当价格接近近期低点（多头止损区）或高点（空头止损区）且波动率较小时，容易触发止损，因子为负；远离止损区且波动率合理时因子为正。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class StopLossAvoidanceFactor(BaseFactor):
    """评估当前价格是否容易触发止损。当价格接近近期低点（多头止损区）或高点（空头止损区）且波动率较小时，容易触发止损，因子为负；远离止损区且波动率合理时因子为正。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_stop_avoidance",
            name="Stop Loss Avoidance Factor",
            display_name="止损规避因子",
            description="评估当前价格是否容易触发止损。当价格接近近期低点（多头止损区）或高点（空头止损区）且波动率较小时，容易触发止损，因子为负；远离止损区且波动率合理时因子为正。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算10日最低价和最高价
        low10 = data['low'].rolling(10).min()
        high10 = data['high'].rolling(10).max()
        # 计算收盘价到近期低点的距离（多头止损距离）和到高点的距离（空头止损距离）
        dist_to_low = (data['close'] - low10) / (data['close'] + 1e-10)
        dist_to_high = (high10 - data['close']) / (data['close'] + 1e-10)
        # 计算ATR
        high_low = data['high'] - data['low']
        high_close = np.abs(data['high'] - data['close'].shift())
        low_close = np.abs(data['low'] - data['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr10 = tr.rolling(10).mean()
        # 距离与ATR比值，越大越不容易触发止损
        long_safety = dist_to_low / (atr10 / data['close'] + 0.001)
        short_safety = dist_to_high / (atr10 / data['close'] + 0.001)
        # 综合多头和空头安全性: 取两者的调和平均值
        combined = 2 * long_safety * short_safety / (long_safety + short_safety + 1e-10)
        # 映射到[-1,1] (当combined很小时为负，表示容易止损)
        result = np.tanh((combined - 1.5) * 0.5)
        return result
