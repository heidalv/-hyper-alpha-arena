"""AI因子: 成交量状态比率 | 置信:55% | 衡量当前成交量相对于近期中位数的偏离程度，结合价格方向。在未知行情中，异常放量常伴随假突破或止损触发，对逆势信号有预警作用。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeRegimeRatio(BaseFactor):
    """衡量当前成交量相对于近期中位数的偏离程度，结合价格方向。在未知行情中，异常放量常伴随假突破或止损触发，对逆势信号有预警作用。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volr",
            name="Volume Regime Ratio",
            display_name="成交量状态比率",
            description="衡量当前成交量相对于近期中位数的偏离程度，结合价格方向。在未知行情中，异常放量常伴随假突破或止损触发，对逆势信号有预警作用。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        vol_med = data['volume'].rolling(20).median()
        vol_ratio = data['volume'] / (vol_med + 1e-9)
        ret = data['close'].pct_change(1)
        result = (vol_ratio * ret).clip(-1, 1)
        return result
