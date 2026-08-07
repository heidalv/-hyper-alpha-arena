"""AI因子: 区间位置与成交量确认 | 置信:50% | 价格在最近N周期高低区间内的位置，当接近区间上沿且成交量萎缩时看空，接近下沿且放量时看多，用于捕捉超买超卖回归"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Range_Position_with_Volume_Confirmation(BaseFactor):
    """价格在最近N周期高低区间内的位置，当接近区间上沿且成交量萎缩时看空，接近下沿且放量时看多，用于捕捉超买超卖回归"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_range_pos",
            name="Range Position with Volume Confirmation",
            display_name="区间位置与成交量确认",
            description="价格在最近N周期高低区间内的位置，当接近区间上沿且成交量萎缩时看空，接近下沿且放量时看多，用于捕捉超买超卖回归",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        n = 20
        high = data['high'].rolling(n).max()
        low = data['low'].rolling(n).min()
        pos = (data['close'] - low) / (high - low + 1e-8)  # 0~1
        # 成交量相对变化
        vol_ma = data['volume'].rolling(20).mean()
        vol_ratio = data['volume'] / (vol_ma + 1e-8)
        # 在高位且量缩 -> 负值；在低位且量增 -> 正值
        result = (1 - pos) * (vol_ratio - 1) * 2  # 粗略缩放
        result = np.clip(result, -1, 1)
        return result.fillna(0)
