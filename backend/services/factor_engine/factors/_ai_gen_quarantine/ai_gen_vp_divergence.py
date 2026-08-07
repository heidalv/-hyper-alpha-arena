"""AI因子: 量价背离因子 | 置信:60% | 计算最近N周期（例如10）的价格变化方向与成交量变化方向的相关性。若价格涨但成交量萎缩（负相关）则输出负值提示假突破；若量价齐升（正相关）输出正值。使用滚动相关系数并归一化到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volumepricedivergence(BaseFactor):
    """计算最近N周期（例如10）的价格变化方向与成交量变化方向的相关性。若价格涨但成交量萎缩（负相关）则输出负值提示假突破；若量价齐升（正相关）输出正值。使用滚动相关系数并归一化到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vp_divergence",
            name="VolumePriceDivergence",
            display_name="量价背离因子",
            description="计算最近N周期（例如10）的价格变化方向与成交量变化方向的相关性。若价格涨但成交量萎缩（负相关）则输出负值提示假突破；若量价齐升（正相关）输出正值。使用滚动相关系数并归一化到[-1,1]。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 价格变化率
        price_chg = data['close'].pct_change().fillna(0)
        # 成交量变化率（用成交额或成交量，这里用volume）
        vol_chg = data['volume'].pct_change().fillna(0)
        # 滚动10期相关系数
        corr = price_chg.rolling(window=10).corr(vol_chg)
        # 直接输出（已在[-1,1]内），NaN填充0
        return corr.fillna(0)
