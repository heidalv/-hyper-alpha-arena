"""AI因子: 超时波动压缩因子 | 置信:65% | 针对max_hold_timeout和sl混合亏损，识别波动率持续压缩后出现的单边滑落。当短期波动率低于长期波动率且价格缓慢下行、持仓时间过长时，波动率压缩导致止损距离过近，容易触发止损。该因子在波动率压缩且价格趋势向下时做空，在波动率压缩且趋势向上时做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Timeoutvolcompression(BaseFactor):
    """针对max_hold_timeout和sl混合亏损，识别波动率持续压缩后出现的单边滑落。当短期波动率低于长期波动率且价格缓慢下行、持仓时间过长时，波动率压缩导致止损距离过近，容易触发止损。该因子在波动率压缩且价格趋势向下时做空，在波动率压缩且趋势向上时做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timeout_vol_compress",
            name="TimeoutVolCompression",
            display_name="超时波动压缩因子",
            description="针对max_hold_timeout和sl混合亏损，识别波动率持续压缩后出现的单边滑落。当短期波动率低于长期波动率且价格缓慢下行、持仓时间过长时，波动率压缩导致止损距离过近，容易触发止损。该因子在波动率压缩且价格趋势向下时做空，在波动率压缩且趋势向上时做多。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        vol_short = data['close'].pct_change().rolling(5).std()
        vol_long = data['close'].pct_change().rolling(30).std()
        vol_compress = vol_short / (vol_long + 1e-9)
        trend = data['close'].pct_change(10)

        result = -trend * (vol_compress < 0.6) * (vol_long < data['close'].pct_change().rolling(60).std())
        result = result / (result.abs().rolling(15).max() + 1e-9)
        return result.clip(-1, 1)
