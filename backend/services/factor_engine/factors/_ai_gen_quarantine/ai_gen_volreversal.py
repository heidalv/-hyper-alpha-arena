"""AI因子: 成交量衰竭反转信号 | 置信:60% | 通过比较当前成交量与近期均值，并结合价格振幅，识别潜在的流动性磁铁反转或多头陷阱。当成交量异常放大但价格震荡收窄时，容易出现反转亏损。正值表示看多，负值表示看空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeExhaustionReversalSignal(BaseFactor):
    """通过比较当前成交量与近期均值，并结合价格振幅，识别潜在的流动性磁铁反转或多头陷阱。当成交量异常放大但价格震荡收窄时，容易出现反转亏损。正值表示看多，负值表示看空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volreversal",
            name="Volume Exhaustion Reversal Signal",
            display_name="成交量衰竭反转信号",
            description="通过比较当前成交量与近期均值，并结合价格振幅，识别潜在的流动性磁铁反转或多头陷阱。当成交量异常放大但价格震荡收窄时，容易出现反转亏损。正值表示看多，负值表示看空。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 20日成交量均值
        vol_ma = data['volume'].rolling(20).mean()
        vol_ratio = data['volume'] / (vol_ma + 1e-10)
        # 价格振幅
        price_range = (data['high'] - data['low']) / data['close'].rolling(50).mean()
        # 成交量放大但振幅缩小 -> 反转风险高
        signal = np.where((vol_ratio > 2) & (price_range < price_range.rolling(20).mean()), -1, 0)
        # 反之，成交量温和且振幅扩大 -> 趋势延续
        signal = np.where((vol_ratio < 1.5) & (price_range > price_range.rolling(20).mean()), 1, signal)
        result = pd.Series(signal, index=data.index)
        return result
