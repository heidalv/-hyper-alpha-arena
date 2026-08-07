"""AI因子: 流动性状态指数 | 置信:60% | 衡量成交量的稳定性，计算近期20日平均成交量与长期100日平均成交量的比值，并取对数绝对值映射到[-1,1]。当成交量异常萎缩或爆量（比值偏离1）时，因子为负，表明流动性异常可能导致未知市场状态，与亏损模式中的regime unknown关联。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityRegimeIndex(BaseFactor):
    """衡量成交量的稳定性，计算近期20日平均成交量与长期100日平均成交量的比值，并取对数绝对值映射到[-1,1]。当成交量异常萎缩或爆量（比值偏离1）时，因子为负，表明流动性异常可能导致未知市场状态，与亏损模式中的regime unknown关联。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_lri",
            name="Liquidity Regime Index",
            display_name="流动性状态指数",
            description="衡量成交量的稳定性，计算近期20日平均成交量与长期100日平均成交量的比值，并取对数绝对值映射到[-1,1]。当成交量异常萎缩或爆量（比值偏离1）时，因子为负，表明流动性异常可能导致未知市场状态，与亏损模式中的regime unknown关联。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        vol_20 = data['volume'].rolling(20).mean()
        vol_100 = data['volume'].rolling(100).mean()
        ratio = vol_20 / vol_100.replace(0, np.nan)
        log_ratio = np.log(ratio.clip(0.01, 100))
        result = -np.abs(log_ratio) / 4.6
        result = result.clip(-1, 1)
        return result
