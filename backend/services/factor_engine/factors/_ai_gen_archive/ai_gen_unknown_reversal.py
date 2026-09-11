"""AI因子: 未知状态反转强度 | 置信:65% | 亏损集中在regime=unknown，且空头亏损多于多头，说明在未知市场状态下趋势跟踪失效，反转信号更有效。该因子衡量短期价格反转强度，结合成交量确认，在未知状态下捕捉超卖反弹或超买回落。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Unknownregimereversal(BaseFactor):
    """亏损集中在regime=unknown，且空头亏损多于多头，说明在未知市场状态下趋势跟踪失效，反转信号更有效。该因子衡量短期价格反转强度，结合成交量确认，在未知状态下捕捉超卖反弹或超买回落。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unknown_reversal",
            name="UnknownRegimeReversal",
            display_name="未知状态反转强度",
            description="亏损集中在regime=unknown，且空头亏损多于多头，说明在未知市场状态下趋势跟踪失效，反转信号更有效。该因子衡量短期价格反转强度，结合成交量确认，在未知状态下捕捉超卖反弹或超买回落。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(3)
        vol_ratio = data['volume'] / (data['volume'].rolling(20).mean() + 1e-9)
        result = (-ret * (vol_ratio - 1)).clip(-1, 1)
        return result
