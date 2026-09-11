"""AI因子: 时间衰减动量 | 置信:55% | 针对超时平仓亏损，强调动量随时间衰减的特性。使用近期动量与远期动量的差值，并乘以成交量变化率，当近期动量弱于远期且成交量萎缩时，因子值偏负，提示持仓价值随时间下降，应尽早平仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Timedecaymomentum(BaseFactor):
    """针对超时平仓亏损，强调动量随时间衰减的特性。使用近期动量与远期动量的差值，并乘以成交量变化率，当近期动量弱于远期且成交量萎缩时，因子值偏负，提示持仓价值随时间下降，应尽早平仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_time_decay_momentum",
            name="TimeDecayMomentum",
            display_name="时间衰减动量",
            description="针对超时平仓亏损，强调动量随时间衰减的特性。使用近期动量与远期动量的差值，并乘以成交量变化率，当近期动量弱于远期且成交量萎缩时，因子值偏负，提示持仓价值随时间下降，应尽早平仓。",
            category="behavioral",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_ret = data['close'].pct_change(5)
        long_ret = data['close'].pct_change(20)
        mom_diff = short_ret - long_ret
        vol_change = data['volume'].pct_change(10)
        result = (mom_diff * vol_change).clip(-1, 1)
        return result
