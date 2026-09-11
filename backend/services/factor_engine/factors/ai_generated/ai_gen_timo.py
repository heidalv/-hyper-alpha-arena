"""AI因子: 超时止损均值回归过滤因子 | 置信:60% | 针对max_hold_timeout亏损模式：持仓超时后价格往往已偏离均值，且成交量萎缩导致无法有效突破。该因子在价格偏离20日均线且成交量低于50日中位数时做反向操作，捕捉均值回归。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TimeoutMeanreversionVolumefilter(BaseFactor):
    """针对max_hold_timeout亏损模式：持仓超时后价格往往已偏离均值，且成交量萎缩导致无法有效突破。该因子在价格偏离20日均线且成交量低于50日中位数时做反向操作，捕捉均值回归。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timo",
            name="TimeOut_MeanReversion_VolumeFilter",
            display_name="超时止损均值回归过滤因子",
            description="针对max_hold_timeout亏损模式：持仓超时后价格往往已偏离均值，且成交量萎缩导致无法有效突破。该因子在价格偏离20日均线且成交量低于50日中位数时做反向操作，捕捉均值回归。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(20)
        ma = data['close'].rolling(20).mean()
        dev = (data['close'] - ma) / (ma + 1e-9)
        vol_ratio = data['volume'] / (data['volume'].rolling(50).median() + 1e-9)
        result = (-dev * (vol_ratio < 0.8).astype(float)).clip(-1, 1)
        return result
