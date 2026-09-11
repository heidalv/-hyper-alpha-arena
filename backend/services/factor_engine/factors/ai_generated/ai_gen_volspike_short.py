"""AI因子: 放量下跌压力 | 置信:65% | 捕捉放量下跌中空头动能增强的形态。通过成交量短期激增与价格下跌的共振，识别空头主导市场。在regime=unknown时，放量下跌往往预示趋势延续，避免逆势做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volumespikeshortpressure(BaseFactor):
    """捕捉放量下跌中空头动能增强的形态。通过成交量短期激增与价格下跌的共振，识别空头主导市场。在regime=unknown时，放量下跌往往预示趋势延续，避免逆势做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volspike_short",
            name="VolumeSpikeShortPressure",
            display_name="放量下跌压力",
            description="捕捉放量下跌中空头动能增强的形态。通过成交量短期激增与价格下跌的共振，识别空头主导市场。在regime=unknown时，放量下跌往往预示趋势延续，避免逆势做多。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(3)
        vol_ratio = data['volume'] / (data['volume'].rolling(20).mean() + 1e-9)
        result = (-ret * vol_ratio).clip(-1, 1)
        return result
