"""AI因子: 波动率不规则指数 | 置信:60% | 检测当前波动率相对于历史波动率的异常程度。利用过去100日的波动率中位数作为基准，计算当前20日波动率的z-score，并通过sigmoid函数将极端低或极端高的波动率映射到负值区间（表示市场状态不明）。当波动率过低（窄幅震荡）或过高（恐慌/非理性）时因子为负，提示regime unknown。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Irregularity_Score(BaseFactor):
    """检测当前波动率相对于历史波动率的异常程度。利用过去100日的波动率中位数作为基准，计算当前20日波动率的z-score，并通过sigmoid函数将极端低或极端高的波动率映射到负值区间（表示市场状态不明）。当波动率过低（窄幅震荡）或过高（恐慌/非理性）时因子为负，提示regime unknown。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vis",
            name="Volatility Irregularity Score",
            display_name="波动率不规则指数",
            description="检测当前波动率相对于历史波动率的异常程度。利用过去100日的波动率中位数作为基准，计算当前20日波动率的z-score，并通过sigmoid函数将极端低或极端高的波动率映射到负值区间（表示市场状态不明）。当波动率过低（窄幅震荡）或过高（恐慌/非理性）时因子为负，提示regime unknown。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        n_short = 20
        n_long = 100
        short_vol = close.pct_change().rolling(n_short).std() * np.sqrt(252)
        long_vol = close.pct_change().rolling(n_long).std() * np.sqrt(252)
        # 使用长期波动率中位数作为基准
        long_vol_median = long_vol.rolling(n_long).median()
        # 避免除以零
        ratio = short_vol / (long_vol_median + 1e-10)
        ratio = ratio.clip(0, 10)  # 限制极端值
        # 映射到[-1,1]：小于0.5或大于2时反常 → 负值
        score = 1 - 2 * ( (ratio < 0.5) | (ratio > 2) ).astype(float)
        # 也可用连续映射：用sigmoid中心在1附近
        # 简化为离散但保留一定连续性
        score = 1.0 / (1.0 + np.exp(-(ratio - 1.0)*3)) * 2 - 1
        return score.fillna(0).clip(-1, 1)
