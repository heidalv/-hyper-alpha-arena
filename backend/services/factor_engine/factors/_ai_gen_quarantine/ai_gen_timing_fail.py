"""AI因子: 时间超时反转 | 置信:60% | 基于波动率萎缩后的爆发：当ATR连续收缩后突然放大，往往对应趋势衰竭或反转。计算最近14根K线的ATR，取其当前值相对于过去14期均值的比率，并乘以前期ATR下降趋势的强度。正值表示波动率爆发且前期萎缩，可能反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TimingFailureReversal(BaseFactor):
    """基于波动率萎缩后的爆发：当ATR连续收缩后突然放大，往往对应趋势衰竭或反转。计算最近14根K线的ATR，取其当前值相对于过去14期均值的比率，并乘以前期ATR下降趋势的强度。正值表示波动率爆发且前期萎缩，可能反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timing_fail",
            name="Timing Failure Reversal",
            display_name="时间超时反转",
            description="基于波动率萎缩后的爆发：当ATR连续收缩后突然放大，往往对应趋势衰竭或反转。计算最近14根K线的ATR，取其当前值相对于过去14期均值的比率，并乘以前期ATR下降趋势的强度。正值表示波动率爆发且前期萎缩，可能反转。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high, low, close = data['high'], data['low'], data['close']
        tr = np.maximum(high - low,
                        np.maximum(abs(high - close.shift(1)),
                                   abs(low - close.shift(1))))
        atr = tr.rolling(14).mean()
        # ATR相对变化率
        atr_ma = atr.rolling(14).mean()
        atr_ratio = atr / (atr_ma + 1e-10)
        # ATR下降趋势：过去N期ATR的线性斜率负值
        slope = atr.diff(5) / 5
        # 当ATR突然放大且前期下降时信号强
        raw = atr_ratio * np.where(slope < 0, -slope, 0)
        # 标准化
        result = np.tanh(raw * 5 - 2)
        return result.fillna(0)
