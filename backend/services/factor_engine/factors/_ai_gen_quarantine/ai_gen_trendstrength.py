"""AI因子: 多周期趋势一致性 | 置信:60% | 分别计算收盘价相对于10日、20日、50日简单移动平均线的位置（正负符号），若三个符号一致则趋势强，输出+1；若不一致或均线纠缠则趋势弱，输出-1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiTimeframeTrendConsistency(BaseFactor):
    """分别计算收盘价相对于10日、20日、50日简单移动平均线的位置（正负符号），若三个符号一致则趋势强，输出+1；若不一致或均线纠缠则趋势弱，输出-1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trendstrength",
            name="Multi-Timeframe Trend Consistency",
            display_name="多周期趋势一致性",
            description="分别计算收盘价相对于10日、20日、50日简单移动平均线的位置（正负符号），若三个符号一致则趋势强，输出+1；若不一致或均线纠缠则趋势弱，输出-1。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ma10 = close.rolling(10).mean()
        ma20 = close.rolling(20).mean()
        ma50 = close.rolling(50).mean()
        sig10 = np.sign(close - ma10)
        sig20 = np.sign(close - ma20)
        sig50 = np.sign(close - ma50)
        sum_sig = sig10 + sig20 + sig50
        # 若三个都相同，sum_sig为3或-3；否则-3到3之间
        result = np.where((sum_sig >= 3) | (sum_sig <= -3), 1.0, -1.0)
        return pd.Series(result, index=data.index)
