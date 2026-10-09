"""AI因子: 动量加速度 | 置信:60% | 短期动量与中期动量的差值，衡量动量加速或减速。差值为正表示近期动量强于中期趋势，未来上涨概率更高；差值为负表示动量衰减，未来下跌概率更高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAcceleration(BaseFactor):
    """短期动量与中期动量的差值，衡量动量加速或减速。差值为正表示近期动量强于中期趋势，未来上涨概率更高；差值为负表示动量衰减，未来下跌概率更高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel",
            name="Momentum Acceleration",
            display_name="动量加速度",
            description="短期动量与中期动量的差值，衡量动量加速或减速。差值为正表示近期动量强于中期趋势，未来上涨概率更高；差值为负表示动量衰减，未来下跌概率更高。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short = data['close'].pct_change(5)
        long = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((short - long) / vol).clip(-1, 1)
        return result
