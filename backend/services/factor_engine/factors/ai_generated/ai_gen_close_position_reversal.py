"""AI因子: 收盘位置反转 | 置信:50% | 收盘价在当日高低区间中的相对位置，接近高点表示强势，接近低点表示弱势。取短期均值后反向，捕捉超买超卖反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ClosePositionReversal(BaseFactor):
    """收盘价在当日高低区间中的相对位置，接近高点表示强势，接近低点表示弱势。取短期均值后反向，捕捉超买超卖反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_close_position_reversal",
            name="Close Position Reversal",
            display_name="收盘位置反转",
            description="收盘价在当日高低区间中的相对位置，接近高点表示强势，接近低点表示弱势。取短期均值后反向，捕捉超买超卖反转。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        rng = (data['high'] - data['low']) + 1e-9
        pos = (data['close'] - data['low']) / rng
        result = (0.5 - pos).rolling(5).mean().clip(-1, 1)
        return result
