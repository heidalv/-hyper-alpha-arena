"""AI因子: 波动率坍塌信号 | 置信:60% | 利用ATR波动率相对收缩与价格偏离均线程度，捕捉波动率急剧下降后的反转倾向。max_hold_timeout常发生在波动率衰竭、价格停滞的阶段，此因子在波动率坍塌且价格偏离时发出反向信号。正值看多，负值看空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityCollapseSignal(BaseFactor):
    """利用ATR波动率相对收缩与价格偏离均线程度，捕捉波动率急剧下降后的反转倾向。max_hold_timeout常发生在波动率衰竭、价格停滞的阶段，此因子在波动率坍塌且价格偏离时发出反向信号。正值看多，负值看空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vcs",
            name="Volatility Collapse Signal",
            display_name="波动率坍塌信号",
            description="利用ATR波动率相对收缩与价格偏离均线程度，捕捉波动率急剧下降后的反转倾向。max_hold_timeout常发生在波动率衰竭、价格停滞的阶段，此因子在波动率坍塌且价格偏离时发出反向信号。正值看多，负值看空。",
            category="volatility",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        # ATR 14
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr14 = tr.rolling(14).mean()
        # Vol ratio: current ATR vs 20-day max ATR
        atr_max20 = atr14.rolling(20).max()
        vol_ratio = atr14 / atr_max20.replace(0, 1e-9)
        # Deviation from 50 SMA
        sma50 = close.rolling(50).mean()
        dev = (close - sma50) / sma50.replace(0, 1e-9)
        # Collapse signal: -dev * (1 - vol_ratio) scaled
        signal = -dev * (1.0 - vol_ratio) * 2.0
        result = signal.clip(-1.0, 1.0)
        return result
