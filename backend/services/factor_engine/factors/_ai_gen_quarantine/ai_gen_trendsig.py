"""AI因子: 多周期趋势一致性 | 置信:72% | 利用短期（5日）、中期（20日）和长期（60日）指数移动平均线的斜率方向，计算三者一致性的程度。当所有周期方向一致时趋势明确，否则容易在regime=unknown中亏损。因子输出为斜率符号乘积乘以斜率强度均值，归一化至[-1,1]"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Multi_Timeframe_Trend_Consistency(BaseFactor):
    """利用短期（5日）、中期（20日）和长期（60日）指数移动平均线的斜率方向，计算三者一致性的程度。当所有周期方向一致时趋势明确，否则容易在regime=unknown中亏损。因子输出为斜率符号乘积乘以斜率强度均值，归一化至[-1,1]"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trendsig",
            name="Multi-Timeframe Trend Consistency",
            display_name="多周期趋势一致性",
            description="利用短期（5日）、中期（20日）和长期（60日）指数移动平均线的斜率方向，计算三者一致性的程度。当所有周期方向一致时趋势明确，否则容易在regime=unknown中亏损。因子输出为斜率符号乘积乘以斜率强度均值，归一化至[-1,1]",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        # 计算不同周期EMA斜率（用线性回归近似，这里简化用价格变化除以时间）
        ema5 = close.ewm(span=5).mean()
        ema20 = close.ewm(span=20).mean()
        ema60 = close.ewm(span=60).mean()
        # 斜率用差分（归一化到价格）
        slope5 = (ema5 - ema5.shift(5)) / ema5.shift(5)
        slope20 = (ema20 - ema20.shift(20)) / ema20.shift(20)
        slope60 = (ema60 - ema60.shift(60)) / ema60.shift(60)
        # 方向符号
        dir5 = np.sign(slope5)
        dir20 = np.sign(slope20)
        dir60 = np.sign(slope60)
        # 一致性得分：三个方向相同则为1，否则为0
        consistency = ((dir5 == dir20) & (dir20 == dir60)).astype(float)
        # 强度：平均斜率绝对值
        strength = (np.abs(slope5) + np.abs(slope20) + np.abs(slope60)) / 3
        # 信号：一致性 * 方向 * 强度
        raw = consistency * dir5 * strength * 100  # 缩放
        result = raw / (raw.abs() + 1e-10) * (1 - np.exp(-raw.abs()))
        return result.fillna(0).clip(-1, 1)
