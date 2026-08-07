"""AI因子: 趋势一致性因子 | 置信:60% | 衡量短中长期趋势方向的一致性。当短期均线（如5周期）和长期均线（如30周期）的方向不一致时（例如短期向上但长期向下），市场处于regime=unknown的混沌状态，因子输出负值。使用均线斜率符号乘积来判断。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendConsistencyFactor(BaseFactor):
    """衡量短中长期趋势方向的一致性。当短期均线（如5周期）和长期均线（如30周期）的方向不一致时（例如短期向上但长期向下），市场处于regime=unknown的混沌状态，因子输出负值。使用均线斜率符号乘积来判断。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_conf",
            name="Trend Consistency Factor",
            display_name="趋势一致性因子",
            description="衡量短中长期趋势方向的一致性。当短期均线（如5周期）和长期均线（如30周期）的方向不一致时（例如短期向上但长期向下），市场处于regime=unknown的混沌状态，因子输出负值。使用均线斜率符号乘积来判断。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算短期和长期均线
        ma_short = data['close'].rolling(window=5).mean()
        ma_long = data['close'].rolling(window=30).mean()
        # 计算均线斜率（用一阶差分符号）
        sign_short = np.sign(ma_short.diff())
        sign_long = np.sign(ma_long.diff())
        # 一致性：两者符号相同为1，相反为-1
        consistency = sign_short * sign_long
        # 处理NaN，用0填充
        result = consistency.fillna(0)
        return result
