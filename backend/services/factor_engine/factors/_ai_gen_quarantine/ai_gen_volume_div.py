"""AI因子: 量价背离因子 | 置信:60% | 衡量价格变化与成交量变化的方向性背离程度。当价格与成交量同向变动时输出正值，背离时输出负值，极端背离接近-1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """衡量价格变化与成交量变化的方向性背离程度。当价格与成交量同向变动时输出正值，背离时输出负值，极端背离接近-1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_div",
            name="Volume-Price Divergence",
            display_name="量价背离因子",
            description="衡量价格变化与成交量变化的方向性背离程度。当价格与成交量同向变动时输出正值，背离时输出负值，极端背离接近-1。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 价格收益率
        ret = close.pct_change()
        # 成交量变化率
        vol_change = volume.pct_change()
        # 计算滚动相关系数（过去10期）
        corr = ret.rolling(10).corr(vol_change)
        # 相关系数在[-1,1]之间，直接作为因子
        result = corr.fillna(0)
        # 当相关系数为NaN时设为0
        return result
