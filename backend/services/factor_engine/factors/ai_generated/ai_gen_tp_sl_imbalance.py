"""AI因子: 止盈止损失衡因子 | 置信:55% | 针对staged_tp1和sl同时亏损的模式，当价格在近期高低点附近反复震荡且波动率放大时，表明止盈止损容易被触发，惩罚此类状态。通过计算价格在近期区间的相对位置与波动率变化。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Tpslimbalance(BaseFactor):
    """针对staged_tp1和sl同时亏损的模式，当价格在近期高低点附近反复震荡且波动率放大时，表明止盈止损容易被触发，惩罚此类状态。通过计算价格在近期区间的相对位置与波动率变化。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tp_sl_imbalance",
            name="TPSLImbalance",
            display_name="止盈止损失衡因子",
            description="针对staged_tp1和sl同时亏损的模式，当价格在近期高低点附近反复震荡且波动率放大时，表明止盈止损容易被触发，惩罚此类状态。通过计算价格在近期区间的相对位置与波动率变化。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high_20 = data['high'].rolling(20).max()
        low_20 = data['low'].rolling(20).min()
        pos = (data['close'] - low_20) / (high_20 - low_20 + 1e-9)
        vol_cur = data['close'].pct_change().rolling(5).std()
        vol_prev = data['close'].pct_change().rolling(20).std()
        vol_change = vol_cur / (vol_prev + 1e-9)
        result = (pos - 0.5).abs() * 2 * (vol_change - 1).clip(-1, 1)
        result = result.rolling(10).mean()
        return result.clip(-1, 1) * -1
