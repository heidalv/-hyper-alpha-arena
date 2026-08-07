"""AI因子: 波动率收缩得分 | 置信:60% | 计算布林带宽度相对于历史水平的百分位，并取负值。当波动率极度收缩（横盘蓄力）时，因子接近-1，预示可能发生耗时震荡导致timeout；波动率扩张时因子接近+1，利于趋势持仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityContractionScore(BaseFactor):
    """计算布林带宽度相对于历史水平的百分位，并取负值。当波动率极度收缩（横盘蓄力）时，因子接近-1，预示可能发生耗时震荡导致timeout；波动率扩张时因子接近+1，利于趋势持仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vc",
            name="Volatility Contraction Score",
            display_name="波动率收缩得分",
            description="计算布林带宽度相对于历史水平的百分位，并取负值。当波动率极度收缩（横盘蓄力）时，因子接近-1，预示可能发生耗时震荡导致timeout；波动率扩张时因子接近+1，利于趋势持仓。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        window = 20
        roll_std = close.rolling(window=window).std()
        roll_mean = close.rolling(window=window).mean()
        bb_width = 2 * roll_std / roll_mean  # relative bandwidth
        # Rolling percentile of bb_width over 100 periods
        bb_rank = bb_width.rolling(window=100).rank(pct=True)
        # Invert and shift to [-1, 1]: low volatility (low rank) -> -1, high volatility -> +1
        result = 2 * (bb_rank - 0.5)
        result = result.clip(-1, 1)
        return result.fillna(0)
