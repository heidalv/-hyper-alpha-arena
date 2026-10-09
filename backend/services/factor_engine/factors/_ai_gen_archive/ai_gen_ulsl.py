"""AI因子: 止损区间价差因子 | 置信:60% | 针对sl止损亏损模式，捕捉价格在近期高低点区间内反复震荡后向不利方向突破的特征。用当前价格在20日区间内的位置，结合波动率扩张程度，识别高概率触发止损的形态。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class UpperLowerStopLossSpread(BaseFactor):
    """针对sl止损亏损模式，捕捉价格在近期高低点区间内反复震荡后向不利方向突破的特征。用当前价格在20日区间内的位置，结合波动率扩张程度，识别高概率触发止损的形态。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ulsl",
            name="Upper_Lower_Stop_Loss_Spread",
            display_name="止损区间价差因子",
            description="针对sl止损亏损模式，捕捉价格在近期高低点区间内反复震荡后向不利方向突破的特征。用当前价格在20日区间内的位置，结合波动率扩张程度，识别高概率触发止损的形态。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high20 = data['high'].rolling(20).max()
        low20 = data['low'].rolling(20).min()
        range_pos = (data['close'] - low20) / (high20 - low20 + 1e-9)
        atr = (data['high'] - data['low']).rolling(14).mean()
        atr_norm = atr / (data['close'].rolling(14).mean() + 1e-9)
        result = ((range_pos - 0.5) * 2 * (1 - atr_norm)).clip(-1, 1)
        return result
