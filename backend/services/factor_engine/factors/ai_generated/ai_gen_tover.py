"""AI因子: 超时离场量比因子 | 置信:55% | 针对max_hold_timeout亏损模式，捕捉持仓时间过长且成交量萎缩导致的流动性枯竭。用短期成交量均值与长期成交量中位数之比，乘以价格动量方向，识别即将因超时触发离场的弱势持仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TimeoutVolumeExitRatio(BaseFactor):
    """针对max_hold_timeout亏损模式，捕捉持仓时间过长且成交量萎缩导致的流动性枯竭。用短期成交量均值与长期成交量中位数之比，乘以价格动量方向，识别即将因超时触发离场的弱势持仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tover",
            name="TimeOut_Volume_Exit_Ratio",
            display_name="超时离场量比因子",
            description="针对max_hold_timeout亏损模式，捕捉持仓时间过长且成交量萎缩导致的流动性枯竭。用短期成交量均值与长期成交量中位数之比，乘以价格动量方向，识别即将因超时触发离场的弱势持仓。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_vol = data['volume'].rolling(5).mean()
        long_vol = data['volume'].rolling(50).median()
        vol_ratio = short_vol / (long_vol + 1e-9)
        ret = data['close'].pct_change(10)
        result = (ret * (1 - vol_ratio)).clip(-1, 1)
        return result
