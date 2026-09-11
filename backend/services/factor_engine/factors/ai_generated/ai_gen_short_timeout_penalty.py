"""AI因子: 空头超时惩罚 | 置信:65% | 捕捉空头持仓超时亏损模式：当价格处于下行趋势但波动率上升时，空头容易因超时止损。该因子在价格低于短期均线且波动率放大时给出负值，提示避免做空或平空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortTimeoutPenalty(BaseFactor):
    """捕捉空头持仓超时亏损模式：当价格处于下行趋势但波动率上升时，空头容易因超时止损。该因子在价格低于短期均线且波动率放大时给出负值，提示避免做空或平空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_short_timeout_penalty",
            name="Short Timeout Penalty",
            display_name="空头超时惩罚",
            description="捕捉空头持仓超时亏损模式：当价格处于下行趋势但波动率上升时，空头容易因超时止损。该因子在价格低于短期均线且波动率放大时给出负值，提示避免做空或平空。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_ma = data['close'].rolling(10).mean()
        long_ma = data['close'].rolling(30).mean()
        trend = (short_ma - long_ma) / (long_ma + 1e-9)
        vol = data['close'].pct_change().rolling(20).std()
        vol_ratio = vol / (data['close'].pct_change().rolling(60).std() + 1e-9)
        result = (-trend * vol_ratio).clip(-1, 1)
        return result
