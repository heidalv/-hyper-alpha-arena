"""AI因子: 做空超时亏损规避 | 置信:62% | 针对做空单因max_hold_timeout亏损且regime=unknown的情况，识别低波动、无趋势、成交量萎缩的横盘状态。当价格在窄幅区间内波动且缺乏方向性动能时，做空容易因时间损耗而亏损。该因子在低ATR、低动量、低成交量时给出负值（建议避免做空），在高波动趋势中给出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortTimeoutLossAvoidance(BaseFactor):
    """针对做空单因max_hold_timeout亏损且regime=unknown的情况，识别低波动、无趋势、成交量萎缩的横盘状态。当价格在窄幅区间内波动且缺乏方向性动能时，做空容易因时间损耗而亏损。该因子在低ATR、低动量、低成交量时给出负值（建议避免做空），在高波动趋势中给出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_short_timeout_avoid",
            name="Short Timeout Loss Avoidance",
            display_name="做空超时亏损规避",
            description="针对做空单因max_hold_timeout亏损且regime=unknown的情况，识别低波动、无趋势、成交量萎缩的横盘状态。当价格在窄幅区间内波动且缺乏方向性动能时，做空容易因时间损耗而亏损。该因子在低ATR、低动量、低成交量时给出负值（建议避免做空），在高波动趋势中给出正值。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(20)
        atr = (data['high'] - data['low']).rolling(20).mean()
        norm_atr = atr / (data['close'].rolling(20).mean() + 1e-9)
        vol_ratio = data['volume'].rolling(20).mean() / (data['volume'].rolling(50).mean() + 1e-9)
        result = (ret * 2 - norm_atr * 10 - (1 - vol_ratio) * 2).clip(-1, 1)
        return result
