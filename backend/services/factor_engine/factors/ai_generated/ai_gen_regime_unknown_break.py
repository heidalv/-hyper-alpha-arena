"""AI因子: 未知状态突破 | 置信:60% | 针对regime=unknown下的亏损，利用价格突破近期窄幅区间但缺乏成交量确认的特征。当价格突破20日高低点但成交量未同步放大时，视为假突破，反向操作；成交量放大则顺势。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeUnknownBreakout(BaseFactor):
    """针对regime=unknown下的亏损，利用价格突破近期窄幅区间但缺乏成交量确认的特征。当价格突破20日高低点但成交量未同步放大时，视为假突破，反向操作；成交量放大则顺势。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_unknown_break",
            name="Regime_Unknown_Breakout",
            display_name="未知状态突破",
            description="针对regime=unknown下的亏损，利用价格突破近期窄幅区间但缺乏成交量确认的特征。当价格突破20日高低点但成交量未同步放大时，视为假突破，反向操作；成交量放大则顺势。",
            category="technical",
            subcategory="breakout",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high_20 = data['high'].rolling(20).max()
        low_20 = data['low'].rolling(20).min()
        mid = (high_20 + low_20) / 2
        price_pos = (data['close'] - mid) / (high_20 - low_20 + 1e-9)
        vol_ratio = data['volume'] / (data['volume'].rolling(20).mean() + 1e-9)
        result = price_pos * (vol_ratio - 1).clip(-1, 1)
        result = result.rolling(2).mean().clip(-1, 1)
        return result
