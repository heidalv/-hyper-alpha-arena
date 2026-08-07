"""AI因子: 效率比噪声滤波器 | 置信:60% | 计算过去N根K线的价格效率比率（净变化除以总路径长度），衡量趋势强度与噪声水平。值接近+1表示强趋势，-1表示纯噪声，用于识别趋势不明朗的噪声环境。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class EfficiencyRatio(BaseFactor):
    """计算过去N根K线的价格效率比率（净变化除以总路径长度），衡量趋势强度与噪声水平。值接近+1表示强趋势，-1表示纯噪声，用于识别趋势不明朗的噪声环境。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_noise_filter",
            name="efficiency_ratio",
            display_name="效率比噪声滤波器",
            description="计算过去N根K线的价格效率比率（净变化除以总路径长度），衡量趋势强度与噪声水平。值接近+1表示强趋势，-1表示纯噪声，用于识别趋势不明朗的噪声环境。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        n = 20
        # 净变化绝对值
        net_change = (close - close.shift(n)).abs()
        # 总路径长度
        total_path = close.diff().abs().rolling(n).sum()
        # 效率比率
        er = net_change / (total_path + 1e-10)
        # 映射到[-1,1]
        result = 2 * er - 1
        return result.fillna(0)
