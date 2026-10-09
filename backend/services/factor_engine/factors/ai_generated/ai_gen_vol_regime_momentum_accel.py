"""AI因子: 波动率状态动量加速度 | 置信:60% | 短周期动量与长周期动量之差（动量加速度），并用已实现波动率做标准化。正值表示近期动量强于中期趋势，可能延续；负值表示动量衰减，可能反转。结合波动率聚集特征提升IC稳定性。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityRegimeMomentumAcceleration(BaseFactor):
    """短周期动量与长周期动量之差（动量加速度），并用已实现波动率做标准化。正值表示近期动量强于中期趋势，可能延续；负值表示动量衰减，可能反转。结合波动率聚集特征提升IC稳定性。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_regime_momentum_accel",
            name="Volatility Regime Momentum Acceleration",
            display_name="波动率状态动量加速度",
            description="短周期动量与长周期动量之差（动量加速度），并用已实现波动率做标准化。正值表示近期动量强于中期趋势，可能延续；负值表示动量衰减，可能反转。结合波动率聚集特征提升IC稳定性。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        mom_short = data['close'].pct_change(5)
        mom_long = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((mom_short - mom_long) / vol).rolling(3).mean().clip(-1, 1)
        return result
