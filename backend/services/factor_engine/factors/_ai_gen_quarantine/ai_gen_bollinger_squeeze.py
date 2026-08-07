"""AI因子: 布林带收缩震荡信号 | 置信:65% | 计算20周期布林带带宽（上轨-下轨）/中轨，当带宽处于过去100周期最低20%分位时，表明市场极度收缩，即将突破但当前处于震荡，易导致止损。输出负信号，带宽越小信号越负。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Bollinger_Band_Squeeze(BaseFactor):
    """计算20周期布林带带宽（上轨-下轨）/中轨，当带宽处于过去100周期最低20%分位时，表明市场极度收缩，即将突破但当前处于震荡，易导致止损。输出负信号，带宽越小信号越负。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bollinger_squeeze",
            name="Bollinger Band Squeeze",
            display_name="布林带收缩震荡信号",
            description="计算20周期布林带带宽（上轨-下轨）/中轨，当带宽处于过去100周期最低20%分位时，表明市场极度收缩，即将突破但当前处于震荡，易导致止损。输出负信号，带宽越小信号越负。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        # 典型价格用于中轨
        tp = (high + low + close) / 3
        sma = tp.rolling(20).mean()
        std = close.rolling(20).std()
        upper = sma + 2 * std
        lower = sma - 2 * std
        bandwidth = (upper - lower) / sma
        # 过去100周期的分位数
        lookback = 100
        rank = bandwidth.rolling(lookback).apply(lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min() + 1e-10), raw=False)
        # 映射：分位数<0.2时负，0.2对应0，0对应-1，0.5以上为正但限制
        result = (rank - 0.2) * 5  # 将0.2映射到0，0映射到-1，0.4映射到1
        result = result.clip(-1, 1)
        return result.fillna(0)
