"""AI因子: 量价相关性 | 置信:60% | 计算过去20天收盘价变化率与成交量变化率的滚动相关系数。量价正相关表明趋势有成交量支持，负相关或弱相关则可能是虚假波动。亏损模式中的未知状态常伴有量价背离。因子直接取相关系数的负值，使得背离时输出负值预警。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Close_Correlation(BaseFactor):
    """计算过去20天收盘价变化率与成交量变化率的滚动相关系数。量价正相关表明趋势有成交量支持，负相关或弱相关则可能是虚假波动。亏损模式中的未知状态常伴有量价背离。因子直接取相关系数的负值，使得背离时输出负值预警。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vcv",
            name="Volume Close Correlation",
            display_name="量价相关性",
            description="计算过去20天收盘价变化率与成交量变化率的滚动相关系数。量价正相关表明趋势有成交量支持，负相关或弱相关则可能是虚假波动。亏损模式中的未知状态常伴有量价背离。因子直接取相关系数的负值，使得背离时输出负值预警。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close_ret = data['close'].pct_change().fillna(0)
        vol_ret = data['volume'].pct_change().fillna(0)
        window = 20
        corr = close_ret.rolling(window).corr(vol_ret)
        factor = -corr  # 背离时负值
        factor = factor.fillna(0)
        return factor
