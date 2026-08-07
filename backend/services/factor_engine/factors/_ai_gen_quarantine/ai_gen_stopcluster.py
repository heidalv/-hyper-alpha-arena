"""AI因子: 止损聚集因子 | 置信:55% | 基于近期止损次数（假设止损发生在价格突破前N根K线高低点）的统计，通过模拟历史止损信号频率，规避频繁触发止损的区域。当近期止损密度高时因子值偏高，提示市场不确定性大。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class StopLossClusterFactor(BaseFactor):
    """基于近期止损次数（假设止损发生在价格突破前N根K线高低点）的统计，通过模拟历史止损信号频率，规避频繁触发止损的区域。当近期止损密度高时因子值偏高，提示市场不确定性大。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_stopcluster",
            name="Stop-Loss Cluster Factor",
            display_name="止损聚集因子",
            description="基于近期止损次数（假设止损发生在价格突破前N根K线高低点）的统计，通过模拟历史止损信号频率，规避频繁触发止损的区域。当近期止损密度高时因子值偏高，提示市场不确定性大。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 模拟止损信号：当价格突破前5根K线最高点或最低点，且反向移动超过0.3%时视为假突破止损
        lookback = 5
        threshold = 0.003
        # 前N期高低
        prev_high = high.rolling(lookback).max().shift(1)
        prev_low = low.rolling(lookback).min().shift(1)
        # 多头止损：突破前高后回落
        long_stop = (close > prev_high) & (close.shift(-1) < close * (1 - threshold))
        short_stop = (close < prev_low) & (close.shift(-1) > close * (1 + threshold))
        stop_signal = (long_stop | short_stop).astype(float)
        # 滚动20期止损次数
        stop_count = stop_signal.rolling(20).sum()
        # 归一化到[-1,1]
        max_count = 20
        result = 2 * (stop_count / max_count) - 1
        return result.fillna(0).clip(-1, 1)
