"""AI因子: 布林带挤压均值回复 | 置信:55% | 当布林带带宽（标准差/移动平均）降至历史低位时，市场即将爆发但方向不明。结合价格相对位置：若价格处于上轨附近则做空（-1），下轨附近做多（+1），中轨附近则中性。避免在未知regime下盲目做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Bollinger_Squeeze_Mean_Reversion(BaseFactor):
    """当布林带带宽（标准差/移动平均）降至历史低位时，市场即将爆发但方向不明。结合价格相对位置：若价格处于上轨附近则做空（-1），下轨附近做多（+1），中轨附近则中性。避免在未知regime下盲目做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bsqz",
            name="Bollinger Squeeze Mean Reversion",
            display_name="布林带挤压均值回复",
            description="当布林带带宽（标准差/移动平均）降至历史低位时，市场即将爆发但方向不明。结合价格相对位置：若价格处于上轨附近则做空（-1），下轨附近做多（+1），中轨附近则中性。避免在未知regime下盲目做多。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ma = close.rolling(20).mean()
        std = close.rolling(20).std()
        bandwidth = std / ma
        # 历史百分位
        bw_percentile = bandwidth.rolling(100).apply(lambda x: (x[-1] - x.min()) / (x.max() - x.min()), raw=True)
        # 价格位置
        z = (close - ma) / std
        # 挤压条件：带宽百分位<0.2 认为挤压
        squeeze = bw_percentile < 0.2
        # 信号：挤压时，z>2做空，z<-2做多，否则中性；非挤压时信号为0
        result = pd.Series(0.0, index=close.index)
        result[squeeze & (z > 2)] = -1.0
        result[squeeze & (z < -2)] = 1.0
        # 其他情况平滑过渡
        result[~squeeze] = 0.0
        return result
