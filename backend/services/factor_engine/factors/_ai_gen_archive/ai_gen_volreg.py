"""AI因子: 波动率状态切换因子 | 置信:60% | 比较短期与长期波动率，判断当前处于波动率扩张还是收缩期。收缩期（因子为正）通常对应低波震荡，扩张期（因子为负）表示高波风险，容易触发止损。该因子可作为交易方向的过滤条件，帮助避开高波动的未知regime。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityRegimeShift(BaseFactor):
    """比较短期与长期波动率，判断当前处于波动率扩张还是收缩期。收缩期（因子为正）通常对应低波震荡，扩张期（因子为负）表示高波风险，容易触发止损。该因子可作为交易方向的过滤条件，帮助避开高波动的未知regime。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volreg",
            name="Volatility Regime Shift",
            display_name="波动率状态切换因子",
            description="比较短期与长期波动率，判断当前处于波动率扩张还是收缩期。收缩期（因子为正）通常对应低波震荡，扩张期（因子为负）表示高波风险，容易触发止损。该因子可作为交易方向的过滤条件，帮助避开高波动的未知regime。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        ret = data['close'].pct_change()
        fast_vol = ret.rolling(5).std()
        slow_vol = ret.rolling(20).std()
        result = (slow_vol - fast_vol) / (slow_vol + fast_vol).replace(0, np.nan)
        return result.fillna(0).clip(-1, 1)
