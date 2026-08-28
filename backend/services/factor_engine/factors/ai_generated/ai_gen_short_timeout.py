"""AI因子: 空头超时压力 | 置信:45% | 亏损以空头为主且多为max_hold_timeout，说明在弱势中逆势做空被套。该因子捕捉短期冲高后快速回落的形态，在反弹乏力时做空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Shorttimeoutpressure(BaseFactor):
    """亏损以空头为主且多为max_hold_timeout，说明在弱势中逆势做空被套。该因子捕捉短期冲高后快速回落的形态，在反弹乏力时做空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_short_timeout",
            name="ShortTimeoutPressure",
            display_name="空头超时压力",
            description="亏损以空头为主且多为max_hold_timeout，说明在弱势中逆势做空被套。该因子捕捉短期冲高后快速回落的形态，在反弹乏力时做空。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high_ret = data['high'].pct_change(3)
        low_ret = data['low'].pct_change(3)
        spread = (high_ret - low_ret).rolling(10).mean()
        close_pos = (data['close'] - data['low']) / (data['high'] - data['low'] + 1e-9)
        result = (-spread * close_pos).clip(-1, 1)
        return result
