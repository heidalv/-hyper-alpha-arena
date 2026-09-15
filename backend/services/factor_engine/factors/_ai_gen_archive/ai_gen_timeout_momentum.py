"""AI因子: 超时动量衰减 | 置信:62% | 针对max_hold_timeout亏损模式，持仓超时往往发生在趋势动能衰竭时。该因子捕捉价格动量减弱且波动率收窄的状态，此时趋势难以为继，容易触发超时止损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TimeoutMomentumDecay(BaseFactor):
    """针对max_hold_timeout亏损模式，持仓超时往往发生在趋势动能衰竭时。该因子捕捉价格动量减弱且波动率收窄的状态，此时趋势难以为继，容易触发超时止损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timeout_momentum",
            name="Timeout Momentum Decay",
            display_name="超时动量衰减",
            description="针对max_hold_timeout亏损模式，持仓超时往往发生在趋势动能衰竭时。该因子捕捉价格动量减弱且波动率收窄的状态，此时趋势难以为继，容易触发超时止损。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        ret_ma = data['close'].pct_change(5).rolling(10).mean()
        vol = data['close'].pct_change().rolling(10).std()
        vol_ma = data['close'].pct_change().rolling(10).std().rolling(20).mean()
        result = (ret - ret_ma).clip(-1, 1) * (vol_ma - vol).clip(-1, 1)
        result = result.fillna(0)
        return result.clip(-1, 1)
