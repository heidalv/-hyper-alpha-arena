"""AI因子: 空头止损突破因子 | 置信:50% | 亏损模式中空头止损（sl）频繁出现，且UNI、XPL等空头亏损，说明在下跌趋势中假突破导致止损。该因子检测价格突破近期低点后快速反弹的形态，给予空头负面信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Shortstoplossbreak(BaseFactor):
    """亏损模式中空头止损（sl）频繁出现，且UNI、XPL等空头亏损，说明在下跌趋势中假突破导致止损。该因子检测价格突破近期低点后快速反弹的形态，给予空头负面信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_short_sl_break",
            name="ShortStopLossBreak",
            display_name="空头止损突破因子",
            description="亏损模式中空头止损（sl）频繁出现，且UNI、XPL等空头亏损，说明在下跌趋势中假突破导致止损。该因子检测价格突破近期低点后快速反弹的形态，给予空头负面信号。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        low = data['low']
        rolling_low = low.rolling(20).min()
        break_down = (close < rolling_low).astype(float)
        rebound = (close > close.shift(1)).astype(float)
        combo = (break_down * rebound).rolling(5).mean()
        result = (0.5 - combo) * 2
        return result.clip(-1, 1)
