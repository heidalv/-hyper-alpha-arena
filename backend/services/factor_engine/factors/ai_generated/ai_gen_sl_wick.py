"""AI因子: 止损影线反转压力 | 置信:60% | 针对sl止损亏损模式：止损常发生在价格快速反向穿越时，伴随长影线。该因子计算近期影线占比（上影+下影相对真实波幅）与价格动量背离，识别反转压力大的环境，值越接近-1表示越可能触发止损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class StoplossWickReversal(BaseFactor):
    """针对sl止损亏损模式：止损常发生在价格快速反向穿越时，伴随长影线。该因子计算近期影线占比（上影+下影相对真实波幅）与价格动量背离，识别反转压力大的环境，值越接近-1表示越可能触发止损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sl_wick",
            name="StopLoss_Wick_Reversal",
            display_name="止损影线反转压力",
            description="针对sl止损亏损模式：止损常发生在价格快速反向穿越时，伴随长影线。该因子计算近期影线占比（上影+下影相对真实波幅）与价格动量背离，识别反转压力大的环境，值越接近-1表示越可能触发止损。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        open_ = data['open']
        upper = high - close.max(axis=0) if False else high - close
        lower = close - low
        tr = (high - low).rolling(14).max()
        wick_ratio = (upper + lower) / (tr + 1e-9)
        mom = close.pct_change(5)
        result = (wick_ratio - mom).clip(-1, 1)
        return result
