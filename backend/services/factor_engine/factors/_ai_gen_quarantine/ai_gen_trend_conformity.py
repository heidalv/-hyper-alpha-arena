"""AI因子: 多周期趋势一致性 | 置信:60% | 衡量短期均线与长期均线方向的一致性，当方向相反时趋势不明确，做多易被震荡止损或超时。输出[-1,1]，方向一致时接近+1，不一致时接近-1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Multi_Timeframe_Trend_Conformity(BaseFactor):
    """衡量短期均线与长期均线方向的一致性，当方向相反时趋势不明确，做多易被震荡止损或超时。输出[-1,1]，方向一致时接近+1，不一致时接近-1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_conformity",
            name="Multi-Timeframe Trend Conformity",
            display_name="多周期趋势一致性",
            description="衡量短期均线与长期均线方向的一致性，当方向相反时趋势不明确，做多易被震荡止损或超时。输出[-1,1]，方向一致时接近+1，不一致时接近-1。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ma_short = close.rolling(5).mean()
        ma_long = close.rolling(20).mean()
        # 计算方向（斜率符号）
        short_slope = ma_short.diff(1)
        long_slope = ma_long.diff(1)
        # 符号乘积：+1同向，-1反向
        sign_prod = np.sign(short_slope) * np.sign(long_slope)
        # 用滚动平均平滑
        result = sign_prod.rolling(3).mean()
        return result
