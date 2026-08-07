"""AI因子: 波动率塌陷 | 置信:70% | 监测波动率收缩程度。当波动率降至历史低位时，价格缺乏动能，持仓极易触发超时止损。因子值[-1,1]，负值代表波动率极度塌陷（避免持仓），正值代表波动率扩张。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityCollapse(BaseFactor):
    """监测波动率收缩程度。当波动率降至历史低位时，价格缺乏动能，持仓极易触发超时止损。因子值[-1,1]，负值代表波动率极度塌陷（避免持仓），正值代表波动率扩张。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vcl",
            name="Volatility Collapse",
            display_name="波动率塌陷",
            description="监测波动率收缩程度。当波动率降至历史低位时，价格缺乏动能，持仓极易触发超时止损。因子值[-1,1]，负值代表波动率极度塌陷（避免持仓），正值代表波动率扩张。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        period = 20
        lookback = 100
        # Bollinger Bands
        sma = close.rolling(period).mean()
        std = close.rolling(period).std()
        upper = sma + 2 * std
        lower = sma - 2 * std
        bandwidth = (upper - lower) / sma
        # percentile rank over lookback
        rank = bandwidth.rolling(lookback).rank(pct=True)
        result = (rank - 0.5) * 2.0
        result = result.fillna(0).clip(-1, 1)
        return result
