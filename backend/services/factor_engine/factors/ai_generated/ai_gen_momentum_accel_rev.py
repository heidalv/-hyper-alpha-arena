"""AI因子: 动量加速度反转 | 置信:60% | 短期动量与长期动量之差（5日收益减20日收益），捕捉动量加速度。当短期显著强于长期时，往往处于超买加速末端，存在均值回归压力；反之超卖加速末端有反弹动能。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAccelerationReversal(BaseFactor):
    """短期动量与长期动量之差（5日收益减20日收益），捕捉动量加速度。当短期显著强于长期时，往往处于超买加速末端，存在均值回归压力；反之超卖加速末端有反弹动能。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel_rev",
            name="Momentum Acceleration Reversal",
            display_name="动量加速度反转",
            description="短期动量与长期动量之差（5日收益减20日收益），捕捉动量加速度。当短期显著强于长期时，往往处于超买加速末端，存在均值回归压力；反之超卖加速末端有反弹动能。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short = data['close'].pct_change(5)
        long = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((short - long) / vol).rolling(3).mean().clip(-1, 1)
        return result
