"""AI因子: 量价相关性 | 置信:60% | 计算过去10个周期内价格变化与成交量变化的相关性，并映射到[-1,1]。当量价走势背离（相关性为负）时，趋势可能不健康，易出现未知状态亏损。正值表示量价同步。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Price_Volume_Correlation(BaseFactor):
    """计算过去10个周期内价格变化与成交量变化的相关性，并映射到[-1,1]。当量价走势背离（相关性为负）时，趋势可能不健康，易出现未知状态亏损。正值表示量价同步。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pvc",
            name="Price-Volume Correlation",
            display_name="量价相关性",
            description="计算过去10个周期内价格变化与成交量变化的相关性，并映射到[-1,1]。当量价走势背离（相关性为负）时，趋势可能不健康，易出现未知状态亏损。正值表示量价同步。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 价格变化率
        price_ret = data['close'].pct_change()
        # 成交量变化率
        vol_ret = data['volume'].pct_change()
        window = 10
        # 滚动相关系数
        corr = price_ret.rolling(window, min_periods=window).corr(vol_ret)
        # 直接输出[-1,1]之间，缺失值填充0
        result = corr.fillna(0.0)
        return result
