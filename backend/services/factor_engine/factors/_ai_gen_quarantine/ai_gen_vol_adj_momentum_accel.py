"""AI因子: 波动调整动量加速度 | 置信:60% | 多周期动量差（5日减20日收益）衡量动量加速/减速，并用已实现波动率归一化，避免高波动期噪声主导。动量加速为正且波动可控时，未来上涨概率更高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentumAcceleration(BaseFactor):
    """多周期动量差（5日减20日收益）衡量动量加速/减速，并用已实现波动率归一化，避免高波动期噪声主导。动量加速为正且波动可控时，未来上涨概率更高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_adj_momentum_accel",
            name="Volatility Adjusted Momentum Acceleration",
            display_name="波动调整动量加速度",
            description="多周期动量差（5日减20日收益）衡量动量加速/减速，并用已实现波动率归一化，避免高波动期噪声主导。动量加速为正且波动可控时，未来上涨概率更高。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        mom_fast = data['close'].pct_change(5)
        mom_slow = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((mom_fast - mom_slow) / vol).clip(-1, 1)
        return result
