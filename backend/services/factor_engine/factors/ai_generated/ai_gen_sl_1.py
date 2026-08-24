"""AI因子: 止损频率 | 置信:60% | 统计标的在一定周期内触发止损的频率，高频止损可能表明策略在该标的上存在较大的风险或亏损倾向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Stoplossfrequency(BaseFactor):
    """统计标的在一定周期内触发止损的频率，高频止损可能表明策略在该标的上存在较大的风险或亏损倾向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sl_1",
            name="StopLossFrequency",
            display_name="止损频率",
            description="统计标的在一定周期内触发止损的频率，高频止损可能表明策略在该标的上存在较大的风险或亏损倾向。",
            category="behavioral",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        sl_count = (data['close'] <= data['stop_loss']).sum()
        total_periods = len(data)
        result = sl_count / total_periods
        return result
