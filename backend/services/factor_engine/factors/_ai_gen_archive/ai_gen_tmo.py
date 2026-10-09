"""AI因子: 超时动量振荡器 | 置信:60% | 捕捉max_hold_timeout亏损模式中，持仓时间过长但动量不足的特征。当价格在窄幅区间震荡且动量衰减时，该因子值趋近-1，提示避免长持仓；动量强劲时趋近+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TimeoutMomentumOscillator(BaseFactor):
    """捕捉max_hold_timeout亏损模式中，持仓时间过长但动量不足的特征。当价格在窄幅区间震荡且动量衰减时，该因子值趋近-1，提示避免长持仓；动量强劲时趋近+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tmo",
            name="timeout_momentum_oscillator",
            display_name="超时动量振荡器",
            description="捕捉max_hold_timeout亏损模式中，持仓时间过长但动量不足的特征。当价格在窄幅区间震荡且动量衰减时，该因子值趋近-1，提示避免长持仓；动量强劲时趋近+1。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        rng = (data['high'].rolling(20).max() - data['low'].rolling(20).min()) / data['close']
        result = (ret / (vol + 1e-9) - rng).clip(-1, 1)
        return result
