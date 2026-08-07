"""AI因子: 趋势效率 | 置信:70% | 衡量价格在滚动窗口内的路径效率，效率低时市场方向不明（regime=unknown），应避免开仓。亏损样本多发生在此类无方向环境中。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendEfficiency(BaseFactor):
    """衡量价格在滚动窗口内的路径效率，效率低时市场方向不明（regime=unknown），应避免开仓。亏损样本多发生在此类无方向环境中。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_teff",
            name="Trend Efficiency",
            display_name="趋势效率",
            description="衡量价格在滚动窗口内的路径效率，效率低时市场方向不明（regime=unknown），应避免开仓。亏损样本多发生在此类无方向环境中。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        L = 20
        smooth = 5
        # 价格位移绝对值 / 单步位移绝对值滚动总和，表示路径效率
        displacement = (close - close.shift(L)).abs()
        path_length = close.diff().abs().rolling(L).sum()
        efficiency = displacement / (path_length + 1e-9)
        # 平滑并映射到[-1, 1]，1为完美趋势，-1为极度震荡
        efficiency_smooth = efficiency.rolling(smooth).mean()
        result = 2 * efficiency_smooth - 1
        return result.fillna(0).clip(-1, 1)
