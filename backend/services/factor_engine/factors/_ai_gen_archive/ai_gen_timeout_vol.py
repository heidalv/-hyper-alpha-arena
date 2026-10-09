"""AI因子: 超时波动交互因子 | 置信:55% | 针对max_hold_timeout亏损，结合波动率和价格位置。当波动率上升且价格处于区间高位时，超时平仓风险大，做空信号。使用ATR-like波动和价格区间位置。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TimeoutVolatilityInteraction(BaseFactor):
    """针对max_hold_timeout亏损，结合波动率和价格位置。当波动率上升且价格处于区间高位时，超时平仓风险大，做空信号。使用ATR-like波动和价格区间位置。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timeout_vol",
            name="Timeout_Volatility_Interaction",
            display_name="超时波动交互因子",
            description="针对max_hold_timeout亏损，结合波动率和价格位置。当波动率上升且价格处于区间高位时，超时平仓风险大，做空信号。使用ATR-like波动和价格区间位置。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        tr = (data['high'] - data['low']).rolling(14).mean()
        atr = tr / (data['close'].rolling(14).mean() + 1e-9)
        pos = (data['close'] - data['low'].rolling(20).min()) / (data['high'].rolling(20).max() - data['low'].rolling(20).min() + 1e-9)
        result = (atr * (pos - 0.5) * 2).clip(-1, 1)
        return result
