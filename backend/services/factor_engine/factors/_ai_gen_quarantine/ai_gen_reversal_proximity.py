"""AI因子: 反转临近指标 | 置信:50% | 基于价格在近期波动区间内的位置，当价格接近区间上沿且成交量缩量，或接近下沿且放量，预示反转概率高。对于做多场景，当价格高位缩量时给出负值，提示可能回调。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Reversal_Proximity(BaseFactor):
    """基于价格在近期波动区间内的位置，当价格接近区间上沿且成交量缩量，或接近下沿且放量，预示反转概率高。对于做多场景，当价格高位缩量时给出负值，提示可能回调。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reversal_proximity",
            name="Reversal Proximity",
            display_name="反转临近指标",
            description="基于价格在近期波动区间内的位置，当价格接近区间上沿且成交量缩量，或接近下沿且放量，预示反转概率高。对于做多场景，当价格高位缩量时给出负值，提示可能回调。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        roll_high = high.rolling(20).max()
        roll_low = low.rolling(20).min()
        position = (close - roll_low) / (roll_high - roll_low + 1e-8)
        # 成交量缩量因子
        vol_ma = volume.rolling(10).mean()
        vol_ratio = volume / vol_ma
        # 强制反转：高位且缩量 => 负值；低位且放量 => 正值
        signal = -(position - 0.5) * (vol_ratio - 1) * 4
        result = np.clip(signal, -1, 1)
        return result
