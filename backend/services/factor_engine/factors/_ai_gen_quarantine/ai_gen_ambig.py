"""AI因子: 趋势模糊度 | 置信:60% | 衡量近期价格趋势的明确程度，通过比较短期收益率波动与趋势强度。当波动大而趋势弱（绝对值收益率均值小）时，市场方向不明确，容易导致做多被止损或止盈亏损。因子值接近-1表示趋势模糊、做多风险高，接近+1表示趋势明确、做多有利。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Ambiguity(BaseFactor):
    """衡量近期价格趋势的明确程度，通过比较短期收益率波动与趋势强度。当波动大而趋势弱（绝对值收益率均值小）时，市场方向不明确，容易导致做多被止损或止盈亏损。因子值接近-1表示趋势模糊、做多风险高，接近+1表示趋势明确、做多有利。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ambig",
            name="Trend Ambiguity",
            display_name="趋势模糊度",
            description="衡量近期价格趋势的明确程度，通过比较短期收益率波动与趋势强度。当波动大而趋势弱（绝对值收益率均值小）时，市场方向不明确，容易导致做多被止损或止盈亏损。因子值接近-1表示趋势模糊、做多风险高，接近+1表示趋势明确、做多有利。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ret = close.pct_change().dropna()
        window = 20
        # 滚动标准差作为波动率
        vol = ret.rolling(window).std()
        # 滚动绝对值均值作为趋势强度
        trend = ret.abs().rolling(window).mean()
        # 避免除零
        ratio = vol / (trend + 1e-10)
        # 归一化到[-1,1]：使用Z-score方法或非线性映射，这里用tanh压缩
        from numpy import tanh
        z = (ratio - ratio.rolling(100).mean()) / (ratio.rolling(100).std() + 1e-10)
        result = -tanh(z)  # 负号：高ratio（模糊）-> -1
        # 填充NaN为0
        result = result.fillna(0).clip(-1, 1)
        return result
