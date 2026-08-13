"""AI因子: 市场混沌指数 | 置信:60% | 基于真实波幅与价格区间的比值计算混沌指数，高值表示市场震荡无序，趋势型策略容易被反复止损。实盘亏损样本中大量出现在unknown regime，表明该状态可能正是高混沌区间。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ChoppinessIndex(BaseFactor):
    """基于真实波幅与价格区间的比值计算混沌指数，高值表示市场震荡无序，趋势型策略容易被反复止损。实盘亏损样本中大量出现在unknown regime，表明该状态可能正是高混沌区间。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_choppy",
            name="Choppiness Index",
            display_name="市场混沌指数",
            description="基于真实波幅与价格区间的比值计算混沌指数，高值表示市场震荡无序，趋势型策略容易被反复止损。实盘亏损样本中大量出现在unknown regime，表明该状态可能正是高混沌区间。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        N = 14
        high = data['high']
        low = data['low']
        close = data['close']
        prev_close = close.shift(1)
        tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        sum_tr = tr.rolling(N).sum()
        price_range = (high.rolling(N).max() - low.rolling(N).min()).replace(0, np.nan)
        ci = 100 * np.log10(sum_tr / price_range) / np.log10(N)
        result = ((ci - 50) / 50).clip(-1, 1).fillna(0)
        return result
