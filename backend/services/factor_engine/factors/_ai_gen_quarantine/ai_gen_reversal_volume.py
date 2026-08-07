"""AI因子: 量价反转强度 | 置信:60% | 基于短期价格与成交量异常识别反转信号。计算当前收盘价相对于前N根K线均值的偏离，并乘以成交量相对均值的比率，再结合价格在布林带中的位置。正值表示看多反转，负值表示看空反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeWeightedReversalStrength(BaseFactor):
    """基于短期价格与成交量异常识别反转信号。计算当前收盘价相对于前N根K线均值的偏离，并乘以成交量相对均值的比率，再结合价格在布林带中的位置。正值表示看多反转，负值表示看空反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reversal_volume",
            name="Volume-weighted Reversal Strength",
            display_name="量价反转强度",
            description="基于短期价格与成交量异常识别反转信号。计算当前收盘价相对于前N根K线均值的偏离，并乘以成交量相对均值的比率，再结合价格在布林带中的位置。正值表示看多反转，负值表示看空反转。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        n = 20
        close = data['close']
        volume = data['volume']
        ma_close = close.rolling(n).mean()
        std_close = close.rolling(n).std()
        ma_vol = volume.rolling(n).mean()
        vol_ratio = volume / ma_vol.clip(lower=1e-8)
        z_score = (close - ma_close) / std_close.clip(lower=1e-8)
        # 反转信号：价格偏离均值且成交量放大时反转概率大
        # 当价格低于均值且成交量放大时看多（+1），反之看空（-1）
        signal = -z_score * vol_ratio
        # 归一化到[-1,1]：使用tanh或clip
        result = signal.clip(-1, 1)
        return result
