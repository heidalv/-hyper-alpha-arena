"""AI因子: 未知状态止损倾向 | 置信:60% | 亏损集中在regime=unknown且触发止损（sl）和master_running_close。当市场状态不明（用短期波动与长期波动比值衡量）且价格突破近期区间时，容易触发止损。该因子在波动结构异常时给出负值，抑制做空或做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Unknownregimestoploss(BaseFactor):
    """亏损集中在regime=unknown且触发止损（sl）和master_running_close。当市场状态不明（用短期波动与长期波动比值衡量）且价格突破近期区间时，容易触发止损。该因子在波动结构异常时给出负值，抑制做空或做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unknown_regime_stop",
            name="UnknownRegimeStopLoss",
            display_name="未知状态止损倾向",
            description="亏损集中在regime=unknown且触发止损（sl）和master_running_close。当市场状态不明（用短期波动与长期波动比值衡量）且价格突破近期区间时，容易触发止损。该因子在波动结构异常时给出负值，抑制做空或做多。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_vol = data['close'].pct_change().rolling(5).std()
        long_vol = data['close'].pct_change().rolling(30).std()
        vol_ratio = short_vol / (long_vol + 1e-9)
        range_pos = (data['close'] - data['low'].rolling(10).min()) / (data['high'].rolling(10).max() - data['low'].rolling(10).min() + 1e-9)
        result = ((vol_ratio - 1) * (range_pos - 0.5) * -2).clip(-1, 1)
        return result
