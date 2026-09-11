"""AI因子: 持仓超时反转强度 | 置信:50% | max_hold_timeout亏损多为空头且小币种，结合短期反转信号和成交量萎缩，识别趋势衰竭后容易横盘超时的行情。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HoldTimeoutReversalStrength(BaseFactor):
    """max_hold_timeout亏损多为空头且小币种，结合短期反转信号和成交量萎缩，识别趋势衰竭后容易横盘超时的行情。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_hold_timeout_reversal",
            name="Hold_Timeout_Reversal_Strength",
            display_name="持仓超时反转强度",
            description="max_hold_timeout亏损多为空头且小币种，结合短期反转信号和成交量萎缩，识别趋势衰竭后容易横盘超时的行情。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret_short = data['close'].pct_change(3)
        ret_long = data['close'].pct_change(15)
        reversal = (ret_short - ret_long).clip(-0.05, 0.05) / 0.05
        vol_ratio = data['volume'] / data['volume'].rolling(20).mean()
        low_vol = (vol_ratio < 0.9).astype(float)
        result = -1 * reversal * low_vol
        return result.clip(-1, 1)
