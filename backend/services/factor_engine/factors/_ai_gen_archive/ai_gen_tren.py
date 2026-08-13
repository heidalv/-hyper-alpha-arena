"""AI因子: 趋势效率因子 | 置信:60% | 基于Kaufman效率比衡量价格趋势的强度与方向。效率比高意味着趋势明显，低意味着震荡。该因子在趋势不明显时接近0，可帮助规避regime=unknown的震荡行情导致的止损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendEfficiencyRatio(BaseFactor):
    """基于Kaufman效率比衡量价格趋势的强度与方向。效率比高意味着趋势明显，低意味着震荡。该因子在趋势不明显时接近0，可帮助规避regime=unknown的震荡行情导致的止损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tren",
            name="Trend Efficiency Ratio",
            display_name="趋势效率因子",
            description="基于Kaufman效率比衡量价格趋势的强度与方向。效率比高意味着趋势明显，低意味着震荡。该因子在趋势不明显时接近0，可帮助规避regime=unknown的震荡行情导致的止损。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        n = 14
        close = data['close']
        change = close.diff(n).abs()
        volatility = close.diff().abs().rolling(n).sum()
        er = change / volatility.replace(0, np.nan)
        direction = np.sign(close.diff(n))
        result = direction * er
        return result.fillna(0).clip(-1, 1)
