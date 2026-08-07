"""AI因子: 量价背离因子 | 置信:60% | 计算滚动窗口内价格变化与成交量变化的相关性，若相关性显著下降或为负，表明量价关系异常，市场处于未知状态，输出负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """计算滚动窗口内价格变化与成交量变化的相关性，若相关性显著下降或为负，表明量价关系异常，市场处于未知状态，输出负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_divergence",
            name="Volume-Price Divergence",
            display_name="量价背离因子",
            description="计算滚动窗口内价格变化与成交量变化的相关性，若相关性显著下降或为负，表明量价关系异常，市场处于未知状态，输出负值。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 价格收益率
        ret = close.pct_change()
        # 成交量变化率
        vol_change = volume.pct_change()
        # 滚动相关系数
        corr = ret.rolling(window=20, min_periods=10).corr(vol_change)
        # 将相关系数映射到[-1,1]，正相关为正，负相关为负
        # 但期望当相关性异常（接近0或负）时为负值，强正相关为正值
        result = corr.fillna(0.0)
        return result.clip(-1.0, 1.0)
