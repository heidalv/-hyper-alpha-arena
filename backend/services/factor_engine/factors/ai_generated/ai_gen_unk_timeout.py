"""AI因子: 未知状态超时均值回归 | 置信:50% | 针对max_hold_timeout亏损，捕捉价格在窄幅区间内反复震荡后向均值回归的倾向，利用布林带位置和成交量萎缩。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class UnknownRegimeTimeoutMeanrev(BaseFactor):
    """针对max_hold_timeout亏损，捕捉价格在窄幅区间内反复震荡后向均值回归的倾向，利用布林带位置和成交量萎缩。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unk_timeout",
            name="Unknown_Regime_Timeout_MeanRev",
            display_name="未知状态超时均值回归",
            description="针对max_hold_timeout亏损，捕捉价格在窄幅区间内反复震荡后向均值回归的倾向，利用布林带位置和成交量萎缩。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ma = data['close'].rolling(20).mean()
        std = data['close'].rolling(20).std()
        z = (data['close'] - ma) / (std + 1e-9)
        vol_ratio = data['volume'].rolling(5).mean() / (data['volume'].rolling(20).mean() + 1e-9)
        result = (-z * (1 - vol_ratio)).clip(-1, 1)
        return result
