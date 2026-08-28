"""AI因子: 量价不对称因子 | 置信:50% | 亏损中short占比高且sl触发，量增时价格下跌可能假突破，用成交量变化与价格变化方向背离度，量增价跌时给负值抑制空头"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volumereturnasymmetry(BaseFactor):
    """亏损中short占比高且sl触发，量增时价格下跌可能假突破，用成交量变化与价格变化方向背离度，量增价跌时给负值抑制空头"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volret",
            name="VolumeReturnAsymmetry",
            display_name="量价不对称因子",
            description="亏损中short占比高且sl触发，量增时价格下跌可能假突破，用成交量变化与价格变化方向背离度，量增价跌时给负值抑制空头",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        ret = close.pct_change(1)
        vol_chg = volume.pct_change(1)
        asym = ret * vol_chg
        result = (asym * -1).clip(-1, 1)
        return result
