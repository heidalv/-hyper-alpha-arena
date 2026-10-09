"""AI因子: 止损多头反转量价因子 | 置信:58% | 针对sl亏损集中在多头（XPL/SOL/ARC）且regime=unknown，结合volume放大但价格下跌的形态，识别假突破或弱势反弹。当价格下跌但成交量异常放大时，多头止损概率高，反向做空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class StoplossLongReversalVolume(BaseFactor):
    """针对sl亏损集中在多头（XPL/SOL/ARC）且regime=unknown，结合volume放大但价格下跌的形态，识别假突破或弱势反弹。当价格下跌但成交量异常放大时，多头止损概率高，反向做空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sl_long_reversal",
            name="StopLoss_Long_Reversal_Volume",
            display_name="止损多头反转量价因子",
            description="针对sl亏损集中在多头（XPL/SOL/ARC）且regime=unknown，结合volume放大但价格下跌的形态，识别假突破或弱势反弹。当价格下跌但成交量异常放大时，多头止损概率高，反向做空。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        price_drop = (data['close'] - data['close'].shift(3)) / (data['close'].shift(3) + 1e-9)
        vol_ratio = data['volume'] / (data['volume'].rolling(20).mean() + 1e-9)
        result = (price_drop * vol_ratio).clip(-1, 1)
        return result
