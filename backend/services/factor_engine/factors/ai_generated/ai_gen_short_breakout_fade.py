"""AI因子: 空头突破衰减 | 置信:60% | 捕捉空头在突破后快速回撤亏损：当价格突破近期高点但随后动能不足（成交量背离）时，做空容易因反弹止损。该因子在价格接近高点但量能无法持续时给出负值，提示避免追空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortBreakoutFade(BaseFactor):
    """捕捉空头在突破后快速回撤亏损：当价格突破近期高点但随后动能不足（成交量背离）时，做空容易因反弹止损。该因子在价格接近高点但量能无法持续时给出负值，提示避免追空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_short_breakout_fade",
            name="Short Breakout Fade",
            display_name="空头突破衰减",
            description="捕捉空头在突破后快速回撤亏损：当价格突破近期高点但随后动能不足（成交量背离）时，做空容易因反弹止损。该因子在价格接近高点但量能无法持续时给出负值，提示避免追空。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high_20 = data['high'].rolling(20).max()
        dist_to_high = (data['close'] - high_20) / (high_20 + 1e-9)
        vol_5 = data['volume'].rolling(5).mean()
        vol_20 = data['volume'].rolling(20).mean()
        volume_div = (vol_5 - vol_20) / (vol_20 + 1e-9)
        result = (dist_to_high * volume_div).clip(-1, 1)
        return result
