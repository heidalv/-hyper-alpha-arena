"""AI因子: 量价背离 | 置信:50% | 价格上行但成交量萎缩(或价格下行但放量)时，趋势可持续性弱，倾向于反转。用价格变动方向与成交量变化的符号一致性构造背离因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格上行但成交量萎缩(或价格下行但放量)时，趋势可持续性弱，倾向于反转。用价格变动方向与成交量变化的符号一致性构造背离因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_div",
            name="Volume Price Divergence",
            display_name="量价背离",
            description="价格上行但成交量萎缩(或价格下行但放量)时，趋势可持续性弱，倾向于反转。用价格变动方向与成交量变化的符号一致性构造背离因子。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        volchg = data['volume'].pct_change(5)
        volstd = data['volume'].pct_change().rolling(20).std() + 1e-9
        result = (-(ret * volchg) / (volstd + 1e-9)).rolling(5).mean().clip(-1, 1)
        return result
