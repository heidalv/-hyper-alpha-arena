"""AI因子: 多周期趋势一致性因子 | 置信:70% | 计算短期（10周期）和长期（30周期）EMA的斜率方向，若方向一致则趋势明确，输出正值；若方向相反则表明市场regime=unknown，易出现止损亏损，输出负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiTimeframeConsistency(BaseFactor):
    """计算短期（10周期）和长期（30周期）EMA的斜率方向，若方向一致则趋势明确，输出正值；若方向相反则表明市场regime=unknown，易出现止损亏损，输出负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_consis",
            name="Multi-Timeframe Consistency",
            display_name="多周期趋势一致性因子",
            description="计算短期（10周期）和长期（30周期）EMA的斜率方向，若方向一致则趋势明确，输出正值；若方向相反则表明市场regime=unknown，易出现止损亏损，输出负值。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        short_period = 10
        long_period = 30
        ema_short = close.ewm(span=short_period, adjust=False).mean()
        ema_long = close.ewm(span=long_period, adjust=False).mean()
        # 斜率：用线性回归或简单差分？用当前值与前值之差
        short_slope = ema_short.diff()
        long_slope = ema_long.diff()
        # 方向：正为1，负为-1，零为0
        short_dir = short_slope.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        long_dir = long_slope.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        # 一致性：方向相乘，同向为1，反向为-1，一个为零则为0
        result = short_dir * long_dir
        return result
