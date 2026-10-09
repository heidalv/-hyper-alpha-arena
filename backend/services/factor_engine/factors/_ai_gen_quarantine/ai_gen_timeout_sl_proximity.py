"""AI因子: 超时止损接近度 | 置信:50% | 亏损中max_hold_timeout和sl同时出现，说明价格在持仓期内缓慢接近止损线但未触发，最终超时平仓。该因子检测价格距近期高低点的接近程度，接近极值且波动收窄时，容易发生超时止损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Timeoutstoplossproximity(BaseFactor):
    """亏损中max_hold_timeout和sl同时出现，说明价格在持仓期内缓慢接近止损线但未触发，最终超时平仓。该因子检测价格距近期高低点的接近程度，接近极值且波动收窄时，容易发生超时止损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timeout_sl_proximity",
            name="TimeoutStopLossProximity",
            display_name="超时止损接近度",
            description="亏损中max_hold_timeout和sl同时出现，说明价格在持仓期内缓慢接近止损线但未触发，最终超时平仓。该因子检测价格距近期高低点的接近程度，接近极值且波动收窄时，容易发生超时止损。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high_20 = data['high'].rolling(20).max()
        low_20 = data['low'].rolling(20).min()
        range_pos = (data['close'] - low_20) / (high_20 - low_20 + 1e-9)
        range_width = (high_20 - low_20) / (data['close'].rolling(20).mean() + 1e-9)
        result = (0.5 - range_pos) * (range_width > 0.02).astype(float)
        result = result.clip(-1, 1)
        return result.fillna(0)
