"""AI因子: 超时成交量停滞因子 | 置信:60% | 针对max_hold_timeout和sl亏损模式，结合成交量萎缩与价格波动率下降。当成交量持续低迷且价格波动收窄时，市场流动性不足，容易导致止损滑点或超时。因子综合量价双重停滞信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TimeoutVolumeStagnation(BaseFactor):
    """针对max_hold_timeout和sl亏损模式，结合成交量萎缩与价格波动率下降。当成交量持续低迷且价格波动收窄时，市场流动性不足，容易导致止损滑点或超时。因子综合量价双重停滞信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timeout_volume",
            name="Timeout_Volume_Stagnation",
            display_name="超时成交量停滞因子",
            description="针对max_hold_timeout和sl亏损模式，结合成交量萎缩与价格波动率下降。当成交量持续低迷且价格波动收窄时，市场流动性不足，容易导致止损滑点或超时。因子综合量价双重停滞信号。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        vol_ma = data['volume'].rolling(5).mean()
        vol_long = data['volume'].rolling(20).mean()
        vol_ratio = vol_ma / (vol_long + 1e-9)
        price_vol = data['close'].pct_change().rolling(5).std()
        price_vol_long = data['close'].pct_change().rolling(20).std()
        price_ratio = price_vol / (price_vol_long + 1e-9)
        stagnation = (1 - vol_ratio) * (1 - price_ratio)
        result = (stagnation * 2 - 1).clip(-1, 1)
        return result
