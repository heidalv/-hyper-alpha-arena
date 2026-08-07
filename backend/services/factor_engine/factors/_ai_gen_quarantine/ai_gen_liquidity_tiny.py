"""AI因子: 流动性微小因子 | 置信:60% | 衡量当前流动性是否充足。计算当日成交量与过去20日均量的比值，经tanh映射到[-1,1]。当成交量显著低于均值时，因子为负值，提示流动性不足，容易发生滑点和微小仓位平仓导致的亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Liquidity_Tiny_Indicator(BaseFactor):
    """衡量当前流动性是否充足。计算当日成交量与过去20日均量的比值，经tanh映射到[-1,1]。当成交量显著低于均值时，因子为负值，提示流动性不足，容易发生滑点和微小仓位平仓导致的亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liquidity_tiny",
            name="Liquidity Tiny Indicator",
            display_name="流动性微小因子",
            description="衡量当前流动性是否充足。计算当日成交量与过去20日均量的比值，经tanh映射到[-1,1]。当成交量显著低于均值时，因子为负值，提示流动性不足，容易发生滑点和微小仓位平仓导致的亏损。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        volume = data['volume']
        vol_ma = volume.rolling(window=20, min_periods=1).mean()
        ratio = volume / vol_ma
        # 将ratio映射到[-1,1]，中心在1附近
        # 当ratio=1时，因子=0；ratio<1时负；ratio>1时正
        factor = np.tanh((ratio - 1) * 2)
        return factor
