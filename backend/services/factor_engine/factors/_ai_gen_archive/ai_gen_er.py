"""AI因子: 趋势效率过滤器 | 置信:60% | 针对regime=unknown时趋势策略易亏损的问题，使用Kaufman效率比衡量价格趋势与噪声的比例。趋势强时接近+1，震荡/未知regime时接近-1，可用于过滤低效率行情。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class EfficiencyRatioRegimeFilter(BaseFactor):
    """针对regime=unknown时趋势策略易亏损的问题，使用Kaufman效率比衡量价格趋势与噪声的比例。趋势强时接近+1，震荡/未知regime时接近-1，可用于过滤低效率行情。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_er",
            name="Efficiency Ratio Regime Filter",
            display_name="趋势效率过滤器",
            description="针对regime=unknown时趋势策略易亏损的问题，使用Kaufman效率比衡量价格趋势与噪声的比例。趋势强时接近+1，震荡/未知regime时接近-1，可用于过滤低效率行情。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        n = 10
        change = data['close'].diff(n).abs()
        volatility = data['close'].diff().abs().rolling(n).sum()
        er = change / volatility.replace(0, np.nan)
        result = (2 * er - 1).fillna(0)
        return result.clip(-1, 1)
