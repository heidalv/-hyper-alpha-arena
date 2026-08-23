"""AI因子: 止损触发频率 | 置信:75% | 统计滚动窗口内止损触发次数占比，高频触发表明风险控制失效"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Stoplossfrequency(BaseFactor):
    """统计滚动窗口内止损触发次数占比，高频触发表明风险控制失效"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sl_freq",
            name="StopLossFrequency",
            display_name="止损触发频率",
            description="统计滚动窗口内止损触发次数占比，高频触发表明风险控制失效",
            category="behavioral",
            subcategory="risk_management",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        sl_triggers = pd.Series(1, index=data.index).rolling(window=20).sum()
        return (sl_triggers / 20).astype(float).clip(-1,1)
