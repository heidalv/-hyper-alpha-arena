"""AI因子: 收盘位置强度 | 置信:55% | 收盘价在近期高低区间中的相对位置，接近上沿表示买方强势(动量延续)，接近下沿表示弱势。用位置减0.5中心化后作为方向因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ClosePositionInRange(BaseFactor):
    """收盘价在近期高低区间中的相对位置，接近上沿表示买方强势(动量延续)，接近下沿表示弱势。用位置减0.5中心化后作为方向因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_close_position",
            name="Close Position in Range",
            display_name="收盘位置强度",
            description="收盘价在近期高低区间中的相对位置，接近上沿表示买方强势(动量延续)，接近下沿表示弱势。用位置减0.5中心化后作为方向因子。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        hi = data['high'].rolling(20).max()
        lo = data['low'].rolling(20).min()
        pos = (data['close'] - lo) / (hi - lo + 1e-9)
        result = (pos - 0.5).clip(-1, 1)
        return result
