"""AI因子: 时间衰减波动率 | 置信:55% | 用近期波动率与长期波动率的比值，结合时间衰减权重，识别市场是否处于不稳定状态。在regime=unknown时，高波动往往导致止损和超时亏损，该因子在波动率异常升高时给出负向信号，提示降低风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Timedecayedvolatility(BaseFactor):
    """用近期波动率与长期波动率的比值，结合时间衰减权重，识别市场是否处于不稳定状态。在regime=unknown时，高波动往往导致止损和超时亏损，该因子在波动率异常升高时给出负向信号，提示降低风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timevol",
            name="TimeDecayedVolatility",
            display_name="时间衰减波动率",
            description="用近期波动率与长期波动率的比值，结合时间衰减权重，识别市场是否处于不稳定状态。在regime=unknown时，高波动往往导致止损和超时亏损，该因子在波动率异常升高时给出负向信号，提示降低风险。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_vol = data['close'].pct_change().rolling(5).std()
        long_vol = data['close'].pct_change().rolling(50).std()
        vol_ratio = short_vol / (long_vol + 1e-9)
        time_weight = 1 - (data['close'].rolling(20).count() / 20) * 0.5
        result = (vol_ratio * time_weight).clip(-1, 1)
        return result
