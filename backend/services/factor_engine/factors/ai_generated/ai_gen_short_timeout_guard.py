"""AI因子: 做空超时止损保护 | 置信:70% | 针对做空单因max_hold_timeout亏损的问题，检测价格在持有期内反弹的强度。当价格从近期低点反弹且波动率上升时，做空风险增大，因子值趋近-1（建议不做空）；当价格持续下跌且波动率低时，因子值趋近+1（适合做空）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortTimeoutGuard(BaseFactor):
    """针对做空单因max_hold_timeout亏损的问题，检测价格在持有期内反弹的强度。当价格从近期低点反弹且波动率上升时，做空风险增大，因子值趋近-1（建议不做空）；当价格持续下跌且波动率低时，因子值趋近+1（适合做空）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_short_timeout_guard",
            name="Short_Timeout_Guard",
            display_name="做空超时止损保护",
            description="针对做空单因max_hold_timeout亏损的问题，检测价格在持有期内反弹的强度。当价格从近期低点反弹且波动率上升时，做空风险增大，因子值趋近-1（建议不做空）；当价格持续下跌且波动率低时，因子值趋近+1（适合做空）。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        low_ret = (data['close'] - data['low'].rolling(5).min()) / (data['low'].rolling(5).min() + 1e-9)
        vol = data['close'].pct_change().rolling(10).std()
        result = (ret - low_ret * 2 - vol * 10).clip(-1, 1)
        return result
