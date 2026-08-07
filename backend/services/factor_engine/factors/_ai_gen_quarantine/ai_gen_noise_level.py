"""AI因子: 价格噪声水平 | 置信:50% | 通过计算价格序列的R/S（重标极差）或简单方法：用连续价格变化的正负相关性来衡量随机性。当噪声高时值为负（-1），趋势强时值为正（+1）。帮助识别高噪声、适合观望的时段。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PriceNoiseLevel(BaseFactor):
    """通过计算价格序列的R/S（重标极差）或简单方法：用连续价格变化的正负相关性来衡量随机性。当噪声高时值为负（-1），趋势强时值为正（+1）。帮助识别高噪声、适合观望的时段。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_noise_level",
            name="Price Noise Level",
            display_name="价格噪声水平",
            description="通过计算价格序列的R/S（重标极差）或简单方法：用连续价格变化的正负相关性来衡量随机性。当噪声高时值为负（-1），趋势强时值为正（+1）。帮助识别高噪声、适合观望的时段。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        ret = close.pct_change().fillna(0)
        # 计算最近10个周期内正收益的比例
        up_ratio = (ret > 0).rolling(10).mean()
        # 用与0.5的偏离程度衡量噪声：接近0.5表示随机噪声 -> -1；偏离0.5表示方向性 -> +1
        # 使用绝对值偏离，取负号
        deviation = (up_ratio - 0.5).abs() * 2  # 0->0, 0.5->1
        result = 2 * deviation - 1  # 映射到[-1,1]: 随机时接近-1，有趋势时接近+1
        return result.fillna(0)
