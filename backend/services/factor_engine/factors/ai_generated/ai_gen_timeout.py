"""AI因子: 超时压力指数 | 置信:50% | max_hold_timeout是主要亏损来源，且regime=unknown。该因子捕捉价格在窄幅区间内反复震荡的时间累积效应，通过价格相对近期高低的偏离度与区间内停留时间（用ATR归一化）构建压力指标，压力越大越可能触发超时止损，给出负向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TimeoutPressureIndex(BaseFactor):
    """max_hold_timeout是主要亏损来源，且regime=unknown。该因子捕捉价格在窄幅区间内反复震荡的时间累积效应，通过价格相对近期高低的偏离度与区间内停留时间（用ATR归一化）构建压力指标，压力越大越可能触发超时止损，给出负向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timeout",
            name="Timeout_Pressure_Index",
            display_name="超时压力指数",
            description="max_hold_timeout是主要亏损来源，且regime=unknown。该因子捕捉价格在窄幅区间内反复震荡的时间累积效应，通过价格相对近期高低的偏离度与区间内停留时间（用ATR归一化）构建压力指标，压力越大越可能触发超时止损，给出负向信号。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high20 = data['high'].rolling(20).max()
        low20 = data['low'].rolling(20).min()
        mid = (high20 + low20) / 2
        range_pos = (data['close'] - mid) / (high20 - low20 + 1e-9)
        atr = (data['high'] - data['low']).rolling(14).mean()
        time_pressure = (data['close'] - data['open']).abs().rolling(10).sum() / (atr * 10 + 1e-9)
        result = -1 * (range_pos * time_pressure).clip(-1, 1)
        return result.fillna(0)
