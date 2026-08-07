"""AI因子: 止损陷阱因子 | 置信:70% | 判断价格是否接近近期极值且成交量萎缩，此时易触发止损订单。计算价格在20日区间内的相对位置，结合成交量相对20日均值的萎缩程度，生成信号：接近上轨且缩量看跌（负值），接近下轨且缩量看涨（正值）。值域[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class StopLossTrap(BaseFactor):
    """判断价格是否接近近期极值且成交量萎缩，此时易触发止损订单。计算价格在20日区间内的相对位置，结合成交量相对20日均值的萎缩程度，生成信号：接近上轨且缩量看跌（负值），接近下轨且缩量看涨（正值）。值域[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_stoptrap",
            name="Stop-Loss Trap",
            display_name="止损陷阱因子",
            description="判断价格是否接近近期极值且成交量萎缩，此时易触发止损订单。计算价格在20日区间内的相对位置，结合成交量相对20日均值的萎缩程度，生成信号：接近上轨且缩量看跌（负值），接近下轨且缩量看涨（正值）。值域[-1,1]。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high'].rolling(20).max()
        low = data['low'].rolling(20).min()
        pos = (data['close'] - low) / (high - low).replace(0, np.nan)
        pos = pos.fillna(0.5)
        vol_ma = data['volume'].rolling(20).mean()
        vol_ratio = data['volume'] / vol_ma
        vol_ratio = vol_ratio.fillna(1)
        # 成交量萎缩: 1 - vol_ratio, 但仅当vol_ratio<1时有效
        shrink = (1 - vol_ratio).clip(0, 1)
        # 极端位置 (0-1归一化)
        extreme = (pos - 0.5).clip(-0.5, 0.5) * 2  # range [-1,1]
        signal = extreme * shrink
        signal = signal.clip(-1, 1)
        signal = signal.fillna(0)
        return signal
