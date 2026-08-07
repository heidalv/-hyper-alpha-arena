"""AI因子: 信噪比 | 置信:60% | 衡量价格运动中的趋势信号与随机噪声之比，低信噪比时市场缺乏明确方向，易发生止损超时等亏损。计算收益率序列的滚动自相关绝对值与波动率的比值，映射到[-1,1]，正值表示信噪比较高（趋势良好），负值表示噪声主导。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Signal_to_Noise_Ratio(BaseFactor):
    """衡量价格运动中的趋势信号与随机噪声之比，低信噪比时市场缺乏明确方向，易发生止损超时等亏损。计算收益率序列的滚动自相关绝对值与波动率的比值，映射到[-1,1]，正值表示信噪比较高（趋势良好），负值表示噪声主导。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_snr",
            name="Signal-to-Noise Ratio",
            display_name="信噪比",
            description="衡量价格运动中的趋势信号与随机噪声之比，低信噪比时市场缺乏明确方向，易发生止损超时等亏损。计算收益率序列的滚动自相关绝对值与波动率的比值，映射到[-1,1]，正值表示信噪比较高（趋势良好），负值表示噪声主导。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change()
        window = 20
        # 滚动自相关系数（滞后1期）的绝对值
        autocorr = ret.rolling(window).apply(lambda x: x.autocorr() if len(x) == window else np.nan, raw=False).abs()
        # 滚动波动率（标准差）
        vol = ret.rolling(window).std()
        # 信噪比 = 自相关 / 波动率（标准化），再取tanh映射到(-1,1)
        snr = autocorr / (vol + 1e-10)
        snr = snr.replace([np.inf, -np.inf], 0).fillna(0)
        # 通过tanh映射到[-1,1]
        result = np.tanh(snr * 10)  # 乘以10使分布更广
        return result
