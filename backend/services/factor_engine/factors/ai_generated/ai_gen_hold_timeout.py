"""AI因子: 持仓超时均值回归 | 置信:45% | max_hold_timeout亏损在多种币种出现，表明在unknown regime中趋势延续性差，持仓过久导致回吐利润。该因子捕捉短期超买/超卖后的反转信号，在价格偏离均线过大时输出反向信号，避免持有过久。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HoldTimeoutMeanReversion(BaseFactor):
    """max_hold_timeout亏损在多种币种出现，表明在unknown regime中趋势延续性差，持仓过久导致回吐利润。该因子捕捉短期超买/超卖后的反转信号，在价格偏离均线过大时输出反向信号，避免持有过久。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_hold_timeout",
            name="Hold_Timeout_Mean_Reversion",
            display_name="持仓超时均值回归",
            description="max_hold_timeout亏损在多种币种出现，表明在unknown regime中趋势延续性差，持仓过久导致回吐利润。该因子捕捉短期超买/超卖后的反转信号，在价格偏离均线过大时输出反向信号，避免持有过久。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ma20 = data['close'].rolling(20).mean()
        zscore = (data['close'] - ma20) / (data['close'].rolling(20).std() + 1e-9)
        volume_ratio = data['volume'] / (data['volume'].rolling(20).mean() + 1e-9)
        result = (-1 * zscore * (1 / (1 + volume_ratio))).clip(-1, 1)
        return result
