"""AI因子: 放量突破确认 | 置信:58% | 当价格突破20日高点且成交量显著放大时，趋势延续概率高；用价格相对20日高点的位置乘以成交量相对中位数的放大倍数，构造量价共振因子，高值预示未来上涨。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeBreakoutConfirmation(BaseFactor):
    """当价格突破20日高点且成交量显著放大时，趋势延续概率高；用价格相对20日高点的位置乘以成交量相对中位数的放大倍数，构造量价共振因子，高值预示未来上涨。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_breakout_confirm_v1",
            name="Volume Breakout Confirmation",
            display_name="放量突破确认",
            description="当价格突破20日高点且成交量显著放大时，趋势延续概率高；用价格相对20日高点的位置乘以成交量相对中位数的放大倍数，构造量价共振因子，高值预示未来上涨。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high20 = data['high'].rolling(20).max()
        pos = (data['close'] - data['low'].rolling(20).min()) / (high20 - data['low'].rolling(20).min() + 1e-9)
        vol_ratio = data['volume'] / (data['volume'].rolling(50).median() + 1e-9)
        result = ((pos - 0.5) * 2 * (vol_ratio - 1)).clip(-1, 1)
        return result
