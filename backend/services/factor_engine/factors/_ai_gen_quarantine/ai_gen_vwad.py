"""AI因子: VWAP偏离度 | 置信:55% | 计算收盘价相对成交量加权平均价格（VWAP）的偏离，使用ATR标准化。偏离过大可能意味着超买或超卖，容易回调。正值表示价格高于VWAP，负值低于。通过tanh压缩至[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VWAPDeviation(BaseFactor):
    """计算收盘价相对成交量加权平均价格（VWAP）的偏离，使用ATR标准化。偏离过大可能意味着超买或超卖，容易回调。正值表示价格高于VWAP，负值低于。通过tanh压缩至[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vwad",
            name="VWAP Deviation",
            display_name="VWAP偏离度",
            description="计算收盘价相对成交量加权平均价格（VWAP）的偏离，使用ATR标准化。偏离过大可能意味着超买或超卖，容易回调。正值表示价格高于VWAP，负值低于。通过tanh压缩至[-1,1]。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        # typical price     tp = (high + low + close) / 3
        vwap = (tp * volume).rolling(window=14, min_periods=1).sum() / volume.rolling(window=14, min_periods=1).sum()
        # deviation     dev = (close - vwap) / vwap
        # ATR normalization     tr = np.maximum(high - low, np.maximum(np.abs(high - close.shift(1)), np.abs(low - close.shift(1))))
        atr = tr.rolling(14, min_periods=1).mean()
        norm = dev * (close / atr)  # scale by inv relative ATR     norm = norm.clip(-3, 3)
        result = np.tanh(norm)
        return result
