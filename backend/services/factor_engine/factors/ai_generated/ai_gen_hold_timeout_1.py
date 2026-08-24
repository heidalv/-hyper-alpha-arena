"""AI因子: 持仓超时频率 | 置信:60% | 统计标的在一定周期内因持仓超时而被平仓的频率，高频超时可能表明策略在该标的上存在较大的持仓风险或流动性问题。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Holdtimeoutfrequency(BaseFactor):
    """统计标的在一定周期内因持仓超时而被平仓的频率，高频超时可能表明策略在该标的上存在较大的持仓风险或流动性问题。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_hold_timeout_1",
            name="HoldTimeoutFrequency",
            display_name="持仓超时频率",
            description="统计标的在一定周期内因持仓超时而被平仓的频率，高频超时可能表明策略在该标的上存在较大的持仓风险或流动性问题。",
            category="behavioral",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        timeout_count = (data['close'] >= data['hold_timeout']).sum()
        total_periods = len(data)
        result = timeout_count / total_periods
        return result
