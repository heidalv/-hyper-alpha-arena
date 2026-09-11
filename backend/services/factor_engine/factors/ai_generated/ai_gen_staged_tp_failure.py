"""AI因子: 分批止盈失败预警因子 | 置信:60% | staged_tp1亏损虽然单笔小但频繁发生，且VIRTUAL的master_running_close亏损较大。该因子通过检测价格在近期高点附近的反复试探失败，以及成交量的异常放大，预测分批止盈可能无法触发的情况。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Stagedtpfailure(BaseFactor):
    """staged_tp1亏损虽然单笔小但频繁发生，且VIRTUAL的master_running_close亏损较大。该因子通过检测价格在近期高点附近的反复试探失败，以及成交量的异常放大，预测分批止盈可能无法触发的情况。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_staged_tp_failure",
            name="StagedTPFailure",
            display_name="分批止盈失败预警因子",
            description="staged_tp1亏损虽然单笔小但频繁发生，且VIRTUAL的master_running_close亏损较大。该因子通过检测价格在近期高点附近的反复试探失败，以及成交量的异常放大，预测分批止盈可能无法触发的情况。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        close = data['close']
        recent_high = high.rolling(10).max()
        proximity = (recent_high - close) / (recent_high + 1e-9)
        vol_spike = data['volume'] / (data['volume'].rolling(10).mean() + 1e-9)
        price_range = (high - data['low']) / (close + 1e-9)
        result = (-proximity * (vol_spike - 1) * price_range).clip(-1, 1)
        return result
