"""AI因子: 量价相关性因子 | 置信:60% | 计算收益率与成交量变化率的滚动相关系数，正值表示量价配合确认趋势，负值表示量价背离。在regime=unknown时，量价相关性多不稳定，该因子可辅助判断行情可信度。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceCorrelation(BaseFactor):
    """计算收益率与成交量变化率的滚动相关系数，正值表示量价配合确认趋势，负值表示量价背离。在regime=unknown时，量价相关性多不稳定，该因子可辅助判断行情可信度。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vqc",
            name="Volume Price Correlation",
            display_name="量价相关性因子",
            description="计算收益率与成交量变化率的滚动相关系数，正值表示量价配合确认趋势，负值表示量价背离。在regime=unknown时，量价相关性多不稳定，该因子可辅助判断行情可信度。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        n = 10
        ret = data['close'].pct_change()
        vol = data['volume'].pct_change()
        corr = ret.rolling(n).corr(vol)
        result = corr.fillna(0).clip(-1, 1)
        return result
