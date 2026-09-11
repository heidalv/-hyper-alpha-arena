"""AI因子: 量价突破确认 | 置信:58% | 收盘位置在近期高低区间中的相对位置乘以成交量相对中位数的放量程度，衡量放量突破的确认强度；放量且收盘靠近区间上沿时因子为正，预示上行动能延续。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceBreakoutConfirmation(BaseFactor):
    """收盘位置在近期高低区间中的相对位置乘以成交量相对中位数的放量程度，衡量放量突破的确认强度；放量且收盘靠近区间上沿时因子为正，预示上行动能延续。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_breakout",
            name="Volume Price Breakout Confirmation",
            display_name="量价突破确认",
            description="收盘位置在近期高低区间中的相对位置乘以成交量相对中位数的放量程度，衡量放量突破的确认强度；放量且收盘靠近区间上沿时因子为正，预示上行动能延续。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        hi = data['high'].rolling(20).max()
        lo = data['low'].rolling(20).min()
        pos = (data['close'] - lo) / (hi - lo + 1e-9)
        volr = data['volume'] / (data['volume'].rolling(50).median() + 1e-9)
        result = ((pos - 0.5) * 2 * (volr - 1)).clip(-1, 1)
        return result
