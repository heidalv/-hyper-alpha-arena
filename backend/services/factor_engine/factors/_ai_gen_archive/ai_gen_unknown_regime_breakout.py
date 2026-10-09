"""AI因子: 未知市场状态突破因子 | 置信:55% | 针对regime=unknown条件下的亏损：这些交易发生在市场状态不明确时，价格突破可能为假突破。该因子通过计算价格突破近期区间的强度与成交量确认度的背离来识别虚假突破：当价格突破但成交量未同步放大，且波动率处于低位时，后续反转概率高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Unknownregimebreakout(BaseFactor):
    """针对regime=unknown条件下的亏损：这些交易发生在市场状态不明确时，价格突破可能为假突破。该因子通过计算价格突破近期区间的强度与成交量确认度的背离来识别虚假突破：当价格突破但成交量未同步放大，且波动率处于低位时，后续反转概率高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unknown_regime_breakout",
            name="UnknownRegimeBreakout",
            display_name="未知市场状态突破因子",
            description="针对regime=unknown条件下的亏损：这些交易发生在市场状态不明确时，价格突破可能为假突破。该因子通过计算价格突破近期区间的强度与成交量确认度的背离来识别虚假突破：当价格突破但成交量未同步放大，且波动率处于低位时，后续反转概率高。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high_20 = data['high'].rolling(20).max()
        low_20 = data['low'].rolling(20).min()
        mid = (high_20 + low_20) / 2
        range_pos = (data['close'] - low_20) / (high_20 - low_20 + 1e-9)
        vol = data['close'].pct_change().rolling(10).std()
        vol_norm = vol / (data['close'].rolling(10).mean() + 1e-9)
        vol_ratio = data['volume'] / (data['volume'].rolling(20).mean() + 1e-9)
        result = ((range_pos - 0.5) * (1 - vol_ratio) * (1 - vol_norm)).clip(-1, 1)
        return result
