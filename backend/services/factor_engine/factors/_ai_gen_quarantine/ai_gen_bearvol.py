"""AI因子: 看跌成交量强度 | 置信:65% | 当价格下跌时，衡量成交量相对于过去20日平均成交量的放大程度。下跌幅度越大、成交量越大，因子值越接近+1，表示空头力量强。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Bearish_Volume_Strength(BaseFactor):
    """当价格下跌时，衡量成交量相对于过去20日平均成交量的放大程度。下跌幅度越大、成交量越大，因子值越接近+1，表示空头力量强。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bearvol",
            name="Bearish Volume Strength",
            display_name="看跌成交量强度",
            description="当价格下跌时，衡量成交量相对于过去20日平均成交量的放大程度。下跌幅度越大、成交量越大，因子值越接近+1，表示空头力量强。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        # 价格变化
        price_change = close.pct_change()
        # 下跌时的成交量倍数
        vol_avg = volume.rolling(20).mean()
        vol_ratio = volume / (vol_avg + 1e-10)
        # 仅考虑下跌
        bear_vol = vol_ratio * (price_change < 0).astype(float) * (-price_change)  # 负价格变化取正
        # 滚动标准化到[-1,1]
        result = bear_vol.rolling(50).apply(lambda x: np.clip((x - x.mean()) / (x.std() + 1e-10), -1, 1), raw=True)
        return result.fillna(0).clip(-1, 1)
