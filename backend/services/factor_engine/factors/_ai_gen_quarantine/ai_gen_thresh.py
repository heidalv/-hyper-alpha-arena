"""AI因子: 微止损阈值因子 | 置信:55% | 基于最近N周期内价格相对区间的微小波动幅度，判断是否容易触发微小止损。当价格接近近期区间边界且波动率低时，容易引发小幅度回调止损，给出负信号（避免入场或做多）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TinyStopLossThreshold(BaseFactor):
    """基于最近N周期内价格相对区间的微小波动幅度，判断是否容易触发微小止损。当价格接近近期区间边界且波动率低时，容易引发小幅度回调止损，给出负信号（避免入场或做多）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_thresh",
            name="TinyStopLossThreshold",
            display_name="微止损阈值因子",
            description="基于最近N周期内价格相对区间的微小波动幅度，判断是否容易触发微小止损。当价格接近近期区间边界且波动率低时，容易引发小幅度回调止损，给出负信号（避免入场或做多）。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 参数
        n = 20
        lookback = 5
        # 计算近期最高最低
        high = data['high']
        low = data['low']
        close = data['close']
        rolling_high = high.rolling(n).max()
        rolling_low = low.rolling(n).min()
        # 当前价格在区间内的位置 (0~1)
        pos = (close - rolling_low) / (rolling_high - rolling_low + 1e-10)
        # 计算最近lookback周期内价格波动幅度（平均真实波幅）
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(lookback).mean()
        # 波动率相对于价格的百分比
        vol_ratio = atr / close * 100
        # 当价格靠近边界（pos接近0或1）且波动率较低时，容易触发小止损
        edge = ((pos < 0.15) | (pos > 0.85)).astype(int)
        low_vol = (vol_ratio < vol_ratio.rolling(50).mean() * 0.7).astype(int)
        signal = -edge * low_vol
        # 标准化到[-1,1]，已满足
        signal = signal * 1.0
        # 处理NaN
        signal = signal.fillna(0.0)
        return signal
