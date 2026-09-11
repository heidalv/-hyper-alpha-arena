"""AI因子: 放量突破反转 | 置信:55% | 亏损中XPL和VIRTUAL出现止损和master_running_close，说明突破后追单易亏损。该因子检测放量突破后立即反转的形态，当价格突破近期高点但成交量异常放大且随后回落，产生反转信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volumebreakreversal(BaseFactor):
    """亏损中XPL和VIRTUAL出现止损和master_running_close，说明突破后追单易亏损。该因子检测放量突破后立即反转的形态，当价格突破近期高点但成交量异常放大且随后回落，产生反转信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_break",
            name="VolumeBreakReversal",
            display_name="放量突破反转",
            description="亏损中XPL和VIRTUAL出现止损和master_running_close，说明突破后追单易亏损。该因子检测放量突破后立即反转的形态，当价格突破近期高点但成交量异常放大且随后回落，产生反转信号。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high_break = data['close'] > data['high'].rolling(10).max().shift(1)
        vol_spike = data['volume'] > data['volume'].rolling(20).mean() * 1.5
        ret_after = data['close'].pct_change(1).shift(-1)
        result = (high_break & vol_spike).astype(float) * ret_after
        result = result.clip(-1, 1)
        return result
