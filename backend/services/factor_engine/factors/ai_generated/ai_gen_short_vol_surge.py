"""AI因子: 空头波动激增 | 置信:55% | 针对VIRTUAL short sl亏损模式：做空止损常发生在波动率突然放大时。因子检测成交量激增与价格下跌的组合，当量价齐跌时给出做空信号，避免在波动率飙升时做空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortVolatilitySurge(BaseFactor):
    """针对VIRTUAL short sl亏损模式：做空止损常发生在波动率突然放大时。因子检测成交量激增与价格下跌的组合，当量价齐跌时给出做空信号，避免在波动率飙升时做空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_short_vol_surge",
            name="Short Volatility Surge",
            display_name="空头波动激增",
            description="针对VIRTUAL short sl亏损模式：做空止损常发生在波动率突然放大时。因子检测成交量激增与价格下跌的组合，当量价齐跌时给出做空信号，避免在波动率飙升时做空。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        vol_ratio = data['volume'] / (data['volume'].rolling(20).median() + 1e-9)
        price_change = data['close'].pct_change(3)
        result = (vol_ratio - 1) * price_change
        result = result.clip(-1, 1)
        return result
