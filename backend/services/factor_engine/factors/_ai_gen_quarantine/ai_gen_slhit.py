"""AI因子: 止损吸引因子 | 置信:60% | 检测价格快速接近最近支撑/阻力位（前10日高低点），同时成交量萎缩（表示流动性不足），容易引发止损单被扫后反转。用价格到近期极点的距离和成交量萎缩程度。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class StopLossMagnet(BaseFactor):
    """检测价格快速接近最近支撑/阻力位（前10日高低点），同时成交量萎缩（表示流动性不足），容易引发止损单被扫后反转。用价格到近期极点的距离和成交量萎缩程度。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_slhit",
            name="Stop Loss Magnet",
            display_name="止损吸引因子",
            description="检测价格快速接近最近支撑/阻力位（前10日高低点），同时成交量萎缩（表示流动性不足），容易引发止损单被扫后反转。用价格到近期极点的距离和成交量萎缩程度。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        lookback = 10
        # 近期高点、低点
        recent_high = data['high'].rolling(lookback).max()
        recent_low = data['low'].rolling(lookback).min()
        # 当前价格与极值的距离（归一化到0-1）
        range_ = recent_high - recent_low
        near_high = (recent_high - data['close']) / (range_ + 1e-8)
        near_low = (data['close'] - recent_low) / (range_ + 1e-8)
        # 成交量萎缩：当前成交量与20日均值之比
        vol_ma20 = data['volume'].rolling(20).mean()
        vol_ratio = data['volume'] / (vol_ma20 + 1e-8)
        # 条件：接近高点（near_high<0.1）且成交量萎缩（vol_ratio<0.8） -> 做空反转
        cond_sell = (near_high < 0.1) & (vol_ratio < 0.8)
        # 接近低点（near_low<0.1）且成交量萎缩 -> 做多反转
        cond_buy = (near_low < 0.1) & (vol_ratio < 0.8)
        signal = pd.Series(0.0, index=data.index)
        signal[cond_buy] = 1.0
        signal[cond_sell] = -1.0
        return signal
