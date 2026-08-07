"""AI因子: 价量背离 | 置信:55% | 计算过去20个周期内价格变化与成交量变化的滚动相关系数。当价格与成交量负相关（系数<0）时，表明价量背离，可能预示虚假行情或未知状态，得分趋近-1；正相关则得分趋近+1。相关系数直接映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Price_Volume_Divergence(BaseFactor):
    """计算过去20个周期内价格变化与成交量变化的滚动相关系数。当价格与成交量负相关（系数<0）时，表明价量背离，可能预示虚假行情或未知状态，得分趋近-1；正相关则得分趋近+1。相关系数直接映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pv",
            name="Price-Volume Divergence",
            display_name="价量背离",
            description="计算过去20个周期内价格变化与成交量变化的滚动相关系数。当价格与成交量负相关（系数<0）时，表明价量背离，可能预示虚假行情或未知状态，得分趋近-1；正相关则得分趋近+1。相关系数直接映射到[-1,1]。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change()
        vol_change = data['volume'].pct_change()
        # 滚动20期相关系数
        corr = ret.rolling(20).corr(vol_change)
        # 直接使用相关系数，并填充NaN为0
        result = corr.fillna(0.0)
        return result
