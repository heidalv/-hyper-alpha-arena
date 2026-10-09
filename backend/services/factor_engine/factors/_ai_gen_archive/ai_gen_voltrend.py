"""AI因子: 量能趋势强度 | 置信:60% | 结合价格趋势和成交量变化，识别趋势的可靠程度。当趋势与成交量放大同步时，信号更强；当趋势与成交量萎缩背离时，信号减弱。针对regime=unknown下的止损和超时亏损，该因子帮助过滤低质量趋势信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volumeadjustedtrendstrength(BaseFactor):
    """结合价格趋势和成交量变化，识别趋势的可靠程度。当趋势与成交量放大同步时，信号更强；当趋势与成交量萎缩背离时，信号减弱。针对regime=unknown下的止损和超时亏损，该因子帮助过滤低质量趋势信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_voltrend",
            name="VolumeAdjustedTrendStrength",
            display_name="量能趋势强度",
            description="结合价格趋势和成交量变化，识别趋势的可靠程度。当趋势与成交量放大同步时，信号更强；当趋势与成交量萎缩背离时，信号减弱。针对regime=unknown下的止损和超时亏损，该因子帮助过滤低质量趋势信号。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(10)
        vol_ratio = data['volume'].rolling(5).mean() / (data['volume'].rolling(20).mean() + 1e-9)
        trend = (data['close'] - data['close'].rolling(20).mean()) / (data['close'].rolling(20).std() + 1e-9)
        result = (ret * vol_ratio + trend * 0.5).clip(-1, 1)
        return result
