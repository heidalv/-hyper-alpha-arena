"""AI因子: 持仓超时均值回归 | 置信:70% | 基于亏损模式中max_hold_timeout频繁出现（BTC/BNB/SOL/ETH/VIRTUAL），且亏损幅度小（-0.05%~-0.63%），表明持仓时间过长导致小亏。因子通过价格偏离移动均线的程度来捕捉均值回归机会，当价格偏离过大时给出反向信号，避免长时间持有趋势性弱的小幅波动。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HoldTimeMeanreversion(BaseFactor):
    """基于亏损模式中max_hold_timeout频繁出现（BTC/BNB/SOL/ETH/VIRTUAL），且亏损幅度小（-0.05%~-0.63%），表明持仓时间过长导致小亏。因子通过价格偏离移动均线的程度来捕捉均值回归机会，当价格偏离过大时给出反向信号，避免长时间持有趋势性弱的小幅波动。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_holdtime",
            name="Hold_Time_MeanReversion",
            display_name="持仓超时均值回归",
            description="基于亏损模式中max_hold_timeout频繁出现（BTC/BNB/SOL/ETH/VIRTUAL），且亏损幅度小（-0.05%~-0.63%），表明持仓时间过长导致小亏。因子通过价格偏离移动均线的程度来捕捉均值回归机会，当价格偏离过大时给出反向信号，避免长时间持有趋势性弱的小幅波动。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ma20 = data['close'].rolling(20).mean()
        ma60 = data['close'].rolling(60).mean()
        dev = (data['close'] - ma20) / (ma20 + 1e-9)
        trend = (ma20 - ma60) / (ma60 + 1e-9)
        result = (-dev * (1 - abs(trend))).clip(-1, 1)
        return result
