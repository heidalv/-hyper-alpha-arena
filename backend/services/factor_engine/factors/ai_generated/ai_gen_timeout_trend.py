"""AI因子: 持仓超时趋势衰竭 | 置信:60% | 多数亏损来自max_hold_timeout，说明趋势持续性差。用长周期动量与短周期波动结合，当动量减弱且波动上升时给出负向信号，避免追涨杀跌。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Timeouttrendexhaustion(BaseFactor):
    """多数亏损来自max_hold_timeout，说明趋势持续性差。用长周期动量与短周期波动结合，当动量减弱且波动上升时给出负向信号，避免追涨杀跌。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timeout_trend",
            name="TimeoutTrendExhaustion",
            display_name="持仓超时趋势衰竭",
            description="多数亏损来自max_hold_timeout，说明趋势持续性差。用长周期动量与短周期波动结合，当动量减弱且波动上升时给出负向信号，避免追涨杀跌。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        mom = data['close'].pct_change(10)
        vol_short = data['close'].pct_change().rolling(5).std()
        vol_long = data['close'].pct_change().rolling(20).std()
        result = (mom * -1) * (vol_short / (vol_long + 1e-9))
        result = result.clip(-1, 1)
        return result
