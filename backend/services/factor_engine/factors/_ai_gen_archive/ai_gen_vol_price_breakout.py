"""AI因子: 量价突破确认 | 置信:58% | 放量突破近期高点时动量更可靠，缩量突破易失败。用成交量相对中位数放大程度与价格突破幅度相乘，衡量放量突破的强度，作为动量延续alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceBreakoutConfirmation(BaseFactor):
    """放量突破近期高点时动量更可靠，缩量突破易失败。用成交量相对中位数放大程度与价格突破幅度相乘，衡量放量突破的强度，作为动量延续alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_breakout",
            name="Volume Price Breakout Confirmation",
            display_name="量价突破确认",
            description="放量突破近期高点时动量更可靠，缩量突破易失败。用成交量相对中位数放大程度与价格突破幅度相乘，衡量放量突破的强度，作为动量延续alpha。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high20 = data['high'].rolling(20).max()
        brk = (data['close'] - high20.shift(1)) / (data['close'].rolling(20).std() + 1e-9)
        volr = data['volume'] / (data['volume'].rolling(50).median() + 1e-9)
        result = (brk * (volr - 1)).rolling(3).mean().clip(-1, 1)
        return result
