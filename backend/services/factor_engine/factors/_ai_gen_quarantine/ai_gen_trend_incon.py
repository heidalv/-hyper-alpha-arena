"""AI因子: 多周期趋势不一致 | 置信:65% | 比较短期（5周期）、中期（10周期）、长期（20周期）均线斜率，若趋势方向不一致则判断为无明确趋势，此时基于价格相对于长期均线的偏离进行均值回归操作。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiPeriodTrendInconsistency(BaseFactor):
    """比较短期（5周期）、中期（10周期）、长期（20周期）均线斜率，若趋势方向不一致则判断为无明确趋势，此时基于价格相对于长期均线的偏离进行均值回归操作。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_incon",
            name="Multi-Period Trend Inconsistency",
            display_name="多周期趋势不一致",
            description="比较短期（5周期）、中期（10周期）、长期（20周期）均线斜率，若趋势方向不一致则判断为无明确趋势，此时基于价格相对于长期均线的偏离进行均值回归操作。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算三个周期的移动平均
        ma5 = data['close'].rolling(5).mean()
        ma10 = data['close'].rolling(10).mean()
        ma20 = data['close'].rolling(20).mean()
        # 斜率通过差分判断方向（1表示上升，-1表示下降）
        slope5 = np.sign(ma5.diff())
        slope10 = np.sign(ma10.diff())
        slope20 = np.sign(ma20.diff())
        # 检查三个方向是否一致（sum绝对值等于3则一致）
        sum_slope = slope5 + slope10 + slope20
        inconsistency = (sum_slope.abs() < 2)  # 至少两个方向不同则认为不一致
        # 计算价格相对于20日均线的偏离度
        deviation = (data['close'] - ma20) / ma20
        # 信号：当趋势不一致时，根据偏离度做均值回归（偏离越大，信号越强）
        signal = pd.Series(0.0, index=data.index)
        signal[inconsistency] = -deviation[inconsistency]  # 价格高于均线则看空，低于则看多
        # 限幅到[-1,1]
        signal = signal.clip(-1, 1)
        return signal
