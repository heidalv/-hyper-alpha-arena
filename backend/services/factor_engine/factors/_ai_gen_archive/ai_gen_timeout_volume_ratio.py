"""AI因子: 超时止损成交量背离因子 | 置信:62% | 基于亏损模式中max_hold_timeout和sl频繁出现，且多发生在regime=unknown（趋势不明）时。该因子检测价格在窄幅震荡中成交量萎缩后突然放量但价格未突破，预示假突破导致的超时止损。通过计算20日价格区间位置与成交量变化率的背离程度，在区间中部且量能异常时给出负面信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TimeoutVolumeContrarian(BaseFactor):
    """基于亏损模式中max_hold_timeout和sl频繁出现，且多发生在regime=unknown（趋势不明）时。该因子检测价格在窄幅震荡中成交量萎缩后突然放量但价格未突破，预示假突破导致的超时止损。通过计算20日价格区间位置与成交量变化率的背离程度，在区间中部且量能异常时给出负面信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timeout_volume_ratio",
            name="Timeout_Volume_Contrarian",
            display_name="超时止损成交量背离因子",
            description="基于亏损模式中max_hold_timeout和sl频繁出现，且多发生在regime=unknown（趋势不明）时。该因子检测价格在窄幅震荡中成交量萎缩后突然放量但价格未突破，预示假突破导致的超时止损。通过计算20日价格区间位置与成交量变化率的背离程度，在区间中部且量能异常时给出负面信号。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high20 = data['high'].rolling(20).max()
        low20 = data['low'].rolling(20).min()
        pos = (data['close'] - low20) / (high20 - low20 + 1e-9)
        vol_ratio = data['volume'] / data['volume'].rolling(20).mean()
        mid_dev = (pos - 0.5).abs()
        result = -1 * (vol_ratio - 1) * (1 - mid_dev * 2)
        result = result.clip(-1, 1)
        return result
