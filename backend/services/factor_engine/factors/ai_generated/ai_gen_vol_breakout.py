"""AI因子: 放量突破确认 | 置信:57% | 收盘位置处于近期高位且成交量显著放大时，视为有效突破，因子值高预示后续上涨概率大。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeConfirmedBreakout(BaseFactor):
    """收盘位置处于近期高位且成交量显著放大时，视为有效突破，因子值高预示后续上涨概率大。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_breakout",
            name="Volume Confirmed Breakout",
            display_name="放量突破确认",
            description="收盘位置处于近期高位且成交量显著放大时，视为有效突破，因子值高预示后续上涨概率大。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        hh = data['high'].rolling(20).max()
        ll = data['low'].rolling(20).min()
        pos = (data['close'] - ll) / (hh - ll + 1e-9)
        vratio = data['volume'] / (data['volume'].rolling(20).mean() + 1e-9)
        result = ((pos - 0.5) * 2 * (vratio - 1)).clip(-1, 1)
        return result
