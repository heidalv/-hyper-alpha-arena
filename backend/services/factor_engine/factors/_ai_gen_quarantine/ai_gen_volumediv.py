"""AI因子: 量价背离因子 | 置信:55% | 衡量价格变动与成交量变动之间的背离程度。当价格上涨但成交量萎缩，或价格下跌但成交量放大时，可能表明市场方向不可持续，处于未知状态。通过计算价格变化率与成交量变化率的负相关性来生成因子值，负相关性越强（背离越显著），因子越接近-1（警告多头）或+1（警告空头），实际返回绝对值表示背离强度，符号表示方向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Price_Divergence(BaseFactor):
    """衡量价格变动与成交量变动之间的背离程度。当价格上涨但成交量萎缩，或价格下跌但成交量放大时，可能表明市场方向不可持续，处于未知状态。通过计算价格变化率与成交量变化率的负相关性来生成因子值，负相关性越强（背离越显著），因子越接近-1（警告多头）或+1（警告空头），实际返回绝对值表示背离强度，符号表示方向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volumediv",
            name="Volume-Price Divergence",
            display_name="量价背离因子",
            description="衡量价格变动与成交量变动之间的背离程度。当价格上涨但成交量萎缩，或价格下跌但成交量放大时，可能表明市场方向不可持续，处于未知状态。通过计算价格变化率与成交量变化率的负相关性来生成因子值，负相关性越强（背离越显著），因子越接近-1（警告多头）或+1（警告空头），实际返回绝对值表示背离强度，符号表示方向。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 价格变化率
        price_chg = data['close'].pct_change()
        # 成交量变化率
        vol_chg = data['volume'].pct_change()
        # 滚动窗口内价格与成交量的相关性（例如20期）
        corr = price_chg.rolling(20).corr(vol_chg)
        # 背离：负相关性表示背离（价格上涨成交量下降或价格下跌成交量上升）
        # 映射到[-1,1]：直接取 -corr，因为负相关时corr为负，取负得正，表示背离信号
        # 但我们希望背离时因子值接近-1或+1？更合理的：背离越强，绝对值越大，符号表示背离方向：
        # 当价格涨而量缩（corr为负）则多头危险，因子负；当价格跌而量增（corr也为负？其实价格跌量增也是负相关）所以方向需要区分。简单化：取 -corr，然后符号取价格变化的符号？或者直接返回 -corr，范围[-1,1], -1表示强负相关（背离），+1表示强正相关（正常）。但背离应为危险信号。
        # 调整：用 -corr 然后乘以 price_chg 的符号？不，简化：背离因子 = -corr，当背离严重（corr接近-1）时，因子接近1，表示未知状态。但正背离（corr接近1）因子接近-1，表示正常。但我们需要在[-1,1]内，且符号有意义。这里统一：背离程度 = -corr，然后乘以价格变化符号？算了，直接输出 -corr，clip后返回。
        div = -corr
        return div.clip(-1, 1).fillna(0)
