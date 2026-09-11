"""AI因子: 未知状态趋势衰减 | 置信:50% | 所有亏损都在regime=unknown，说明当前市场状态不明确，趋势信号失效。该因子衡量趋势强度衰减：计算短期动量与长期动量之差，当短期动量弱于长期但价格仍处于高位时，预测下跌。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Regimeunknowntrenddecay(BaseFactor):
    """所有亏损都在regime=unknown，说明当前市场状态不明确，趋势信号失效。该因子衡量趋势强度衰减：计算短期动量与长期动量之差，当短期动量弱于长期但价格仍处于高位时，预测下跌。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regimeunknown",
            name="RegimeUnknownTrendDecay",
            display_name="未知状态趋势衰减",
            description="所有亏损都在regime=unknown，说明当前市场状态不明确，趋势信号失效。该因子衡量趋势强度衰减：计算短期动量与长期动量之差，当短期动量弱于长期但价格仍处于高位时，预测下跌。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ret_short = close.pct_change(5)
        ret_long = close.pct_change(20)
        decay = ret_short - ret_long
        price_pos = (close - close.rolling(20).min()) / (close.rolling(20).max() - close.rolling(20).min() + 1e-9)
        result = (decay * (1 - price_pos)).clip(-1, 1)
        return result
