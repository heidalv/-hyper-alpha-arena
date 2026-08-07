"""AI因子: 多周期趋势一致性 | 置信:65% | 比较短期(5日)、中期(20日)、长期(60日)指数移动平均线的排列方向与斜率。当三者方向一致时做多胜率高，混乱时避免做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Consistency(BaseFactor):
    """比较短期(5日)、中期(20日)、长期(60日)指数移动平均线的排列方向与斜率。当三者方向一致时做多胜率高，混乱时避免做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_con",
            name="Trend Consistency",
            display_name="多周期趋势一致性",
            description="比较短期(5日)、中期(20日)、长期(60日)指数移动平均线的排列方向与斜率。当三者方向一致时做多胜率高，混乱时避免做多。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ema5 = data['close'].ewm(span=5).mean()
        ema20 = data['close'].ewm(span=20).mean()
        ema60 = data['close'].ewm(span=60).mean()
        # 计算斜率：当前值 - 前值
        slp5 = ema5 - ema5.shift(1)
        slp20 = ema20 - ema20.shift(1)
        slp60 = ema60 - ema60.shift(1)
        # 趋势方向：1代表向上，-1向下
        dir5 = (slp5 > 0).astype(int) * 2 - 1
        dir20 = (slp20 > 0).astype(int) * 2 - 1
        dir60 = (slp60 > 0).astype(int) * 2 - 1
        # 一致性得分：三个方向一致为1.0，两个一致为0.5，全不一致为-1
        sum_dir = dir5 + dir20 + dir60
        consistency = sum_dir / 3.0  # 范围-1到1
        # 加权利重：近期斜率大小影响
        weight = (slp5.abs() + slp20.abs() + slp60.abs()) / (ema5 + ema20 + ema60) * 100
        result = consistency * (1 + np.tanh(weight - weight.mean())/2)  # 微调
        result = result.clip(-1,1)
        return result.fillna(0)
