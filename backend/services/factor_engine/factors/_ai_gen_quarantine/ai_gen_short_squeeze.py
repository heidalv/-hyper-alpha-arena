"""AI因子: 空头挤压强度 | 置信:65% | 通过价格快速突破前期高点、成交量放大、日内振幅收窄等特征，识别潜在的空头挤压行情。当因子接近+1时预示强烈的空头回补压力，接近-1时表示空头主导。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortSqueezeIntensity(BaseFactor):
    """通过价格快速突破前期高点、成交量放大、日内振幅收窄等特征，识别潜在的空头挤压行情。当因子接近+1时预示强烈的空头回补压力，接近-1时表示空头主导。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_short_squeeze",
            name="short_squeeze_intensity",
            display_name="空头挤压强度",
            description="通过价格快速突破前期高点、成交量放大、日内振幅收窄等特征，识别潜在的空头挤压行情。当因子接近+1时预示强烈的空头回补压力，接近-1时表示空头主导。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 参数
        lookback = 5
        # 计算过去N天最高价突破幅度
        rolling_high = data['high'].rolling(lookback).max().shift(1)
        breakout = (data['high'] - rolling_high) / rolling_high
        # 成交量异常放大 (当前成交量/过去N日均量)
        avg_vol = data['volume'].rolling(lookback).mean().shift(1)
        vol_ratio = data['volume'] / (avg_vol + 1e-10)
        # 日内振幅比例 (high-low)/close
        intraday_range = (data['high'] - data['low']) / data['close']
        # 收盘价相对于开盘价的位置 (上行强度)
        upward_strength = (data['close'] - data['open']) / (data['high'] - data['low'] + 1e-10)
        # 综合得分：突破 + 成交量放大 - 振幅过大(假突破) + 上行强度
        score = breakout.clip(-0.1, 0.1) * 5 + (vol_ratio - 1).clip(-2, 2) * 0.3 + upward_strength.clip(-1, 1) * 0.5 - intraday_range.clip(0, 0.1) * 10
        # 归一化到[-1,1]
        result = score / (score.abs().rolling(lookback*2).mean() + 1e-10)
        result = result.clip(-1, 1)
        return result
