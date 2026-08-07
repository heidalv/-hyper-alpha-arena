"""AI因子: 量价背离指数 | 置信:60% | 检测价格与成交量之间的背离关系，背离常预示趋势减弱或反转，对应regime unknown状态。计算：短期价格变化方向与成交量变化方向不一致时输出负值。具体：20期价格动量（close-close.shift(20)）归一化后与20期成交量变化率（volume/volume.shift(20)-1）的相关系数，符号取反。正相关表示量价配合，输出正值；负相关（背离）输出负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """检测价格与成交量之间的背离关系，背离常预示趋势减弱或反转，对应regime unknown状态。计算：短期价格变化方向与成交量变化方向不一致时输出负值。具体：20期价格动量（close-close.shift(20)）归一化后与20期成交量变化率（volume/volume.shift(20)-1）的相关系数，符号取反。正相关表示量价配合，输出正值；负相关（背离）输出负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_diverg",
            name="Volume-Price Divergence",
            display_name="量价背离指数",
            description="检测价格与成交量之间的背离关系，背离常预示趋势减弱或反转，对应regime unknown状态。计算：短期价格变化方向与成交量变化方向不一致时输出负值。具体：20期价格动量（close-close.shift(20)）归一化后与20期成交量变化率（volume/volume.shift(20)-1）的相关系数，符号取反。正相关表示量价配合，输出正值；负相关（背离）输出负值。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        if len(data) < 20:
            return pd.Series(0, index=data.index)
        close = data['close']
        volume = data['volume']
        # 价格动量
        price_mom = close.pct_change(20)
        # 成交量变化率
        vol_change = volume.pct_change(20)
        # 滚动相关系数（20期）
        corr = price_mom.rolling(20).corr(vol_change)
        # 当相关系数较低或负值时，背离严重，输出负值
        factor = -corr  # 负相关系数->正背离？需要仔细：我们希望背离时负值，即corr为负时factor为正？错误。
        # 实际：背离即corr接近-1，我们希望因子接近-1；一致时corr接近1，因子接近+1。因此直接用corr
        factor = corr
        # 填充NaN
        factor = factor.fillna(0)
        return factor
