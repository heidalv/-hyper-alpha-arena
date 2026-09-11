"""AI因子: 超时逆势反转因子 | 置信:50% | max_hold_timeout亏损集中在SOL/BNB/UNI/ASTER/XPL，且方向有long有short，表明在趋势不明朗时持仓超时被双向打脸。本因子捕捉短期价格偏离均线过远后的回归倾向，在超时亏损高发场景（震荡市）中做均值回归。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Timeoutcontrarianmomentum(BaseFactor):
    """max_hold_timeout亏损集中在SOL/BNB/UNI/ASTER/XPL，且方向有long有short，表明在趋势不明朗时持仓超时被双向打脸。本因子捕捉短期价格偏离均线过远后的回归倾向，在超时亏损高发场景（震荡市）中做均值回归。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timeout_contrarian",
            name="TimeoutContrarianMomentum",
            display_name="超时逆势反转因子",
            description="max_hold_timeout亏损集中在SOL/BNB/UNI/ASTER/XPL，且方向有long有short，表明在趋势不明朗时持仓超时被双向打脸。本因子捕捉短期价格偏离均线过远后的回归倾向，在超时亏损高发场景（震荡市）中做均值回归。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ma = data['close'].rolling(10).mean()
        dev = (data['close'] - ma) / (ma + 1e-9)
        vol = data['close'].pct_change().rolling(10).std()
        result = (-dev / (vol + 1e-9)).clip(-1, 1)
        return result
