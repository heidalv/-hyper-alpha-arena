"""AI因子: 趋势清晰度指标 | 置信:60% | 计算短期价格与长期均线的乖离率，并除以近期波动率标准化，衡量趋势强度。当趋势不明显（乖离率接近0）时因子值负向，建议避免做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Clarity_Indicator(BaseFactor):
    """计算短期价格与长期均线的乖离率，并除以近期波动率标准化，衡量趋势强度。当趋势不明显（乖离率接近0）时因子值负向，建议避免做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_fuzzy",
            name="Trend Clarity Indicator",
            display_name="趋势清晰度指标",
            description="计算短期价格与长期均线的乖离率，并除以近期波动率标准化，衡量趋势强度。当趋势不明显（乖离率接近0）时因子值负向，建议避免做多。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ma20 = close.rolling(20).mean()
        ma100 = close.rolling(100).mean()
        # 价格相对于长期均线的偏离百分比
        deviation = (close - ma100) / ma100
        # 用近20日ATR标准化
        tr = pd.concat([data['high'] - data['low'], (data['high'] - close.shift(1)).abs(), (data['low'] - close.shift(1)).abs()], axis=1).max(axis=1)
        atr20 = tr.rolling(20).mean()
        std_dev = atr20 / ma100  # 百分比形式的波动
        # 信号：偏离越小（接近0），趋势越模糊，因子值负；偏离大则正
        raw = deviation / (std_dev + 1e-6)
        # 用tanh压缩到[-1,1]，同时取负号使模糊时负值
        result = np.tanh(raw) * -1  # 注意：当raw接近0时tanh~0，取负仍为0，但我们需要负值表示模糊？实际上我们希望模糊时负值，但raw=0时tanh=0，取负后还是0，不太合适。改用：当|raw|小时为负，大时为正。使用1 - exp(-|raw|)再取符号？更好：直接用raw的绝对值取反？
        # 改为：先用atan_scaled，使0附近输出0，再取负号？重新设计：
        # 计算乖离率的绝对值，越小越模糊，输出负值；越大越清晰输出正值。
        abs_dev = deviation.abs()
        norm_abs = abs_dev / (std_dev + 1e-6)
        # 使用sigmoid映射到[-1,1]：当norm_abs=0时输出-1，大时输出1
        result = (1 - np.exp(-norm_abs)) / (1 + np.exp(-norm_abs)) * 2 - 1  # 实际上这是tanh(norm_abs)
        # tanh(norm_abs)范围[0,1)，需要映射到[-1,1]：2*tanh -1 则0时-1，大时接近1
        result = 2 * np.tanh(norm_abs) - 1
        return result.fillna(0)
