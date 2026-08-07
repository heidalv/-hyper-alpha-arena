"""AI因子: 成交量方向离散度 | 置信:60% | 检测成交量和价格变动之间的方向一致性。当成交量放大但价格变动很小（即无方向性）时，因子为负值，预示regime unknown。计算近期价格变动的绝对值和成交量标准化后的相关系数。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Price_Discrepancy(BaseFactor):
    """检测成交量和价格变动之间的方向一致性。当成交量放大但价格变动很小（即无方向性）时，因子为负值，预示regime unknown。计算近期价格变动的绝对值和成交量标准化后的相关系数。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_voldis",
            name="Volume-Price Discrepancy",
            display_name="成交量方向离散度",
            description="检测成交量和价格变动之间的方向一致性。当成交量放大但价格变动很小（即无方向性）时，因子为负值，预示regime unknown。计算近期价格变动的绝对值和成交量标准化后的相关系数。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        ret = close.pct_change().fillna(0)
        # 计算10周期内价格变动绝对值与成交量的秩相关系数
        def rolling_spearman(series1, series2, window):
            return series1.rolling(window).corr(series2, method='spearman')
        abs_ret = ret.abs()
        # 使用斯皮尔曼相关系数更稳健
        corr = rolling_spearman(abs_ret, volume, 10)
        # 填充缺失值
        corr = corr.fillna(0)
        # 当相关性强（绝对值接近1）表示方向明确，因子为正；相关性弱则负
        # 映射到[-1,1]：corr本身就在[-1,1]
        result = corr
        return result
