"""AI因子: 量价协同因子 | 置信:50% | 衡量成交量和价格变动的协同程度。当价格有明确方向且成交量放大时因子为正，成交量与价格方向不一致或成交量萎缩时因子为负。使用Short-term相关系数，再映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceAlignment(BaseFactor):
    """衡量成交量和价格变动的协同程度。当价格有明确方向且成交量放大时因子为正，成交量与价格方向不一致或成交量萎缩时因子为负。使用Short-term相关系数，再映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_corr",
            name="Volume-Price Alignment",
            display_name="量价协同因子",
            description="衡量成交量和价格变动的协同程度。当价格有明确方向且成交量放大时因子为正，成交量与价格方向不一致或成交量萎缩时因子为负。使用Short-term相关系数，再映射到[-1,1]。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        window = 20
        close = data['close']
        volume = data['volume']
        ret = close.pct_change()
        vol_change = volume.pct_change()
        corr = ret.rolling(window).corr(vol_change)
        factor = corr.fillna(0).clip(-1, 1)
        return factor
