"""AI因子: 波动突破反转 | 置信:55% | 计算近期ATR扩张比率与价格回调幅度，当波动率突然放大但价格未能持续时发出空头信号，反之多头信号。捕捉市场状态不明时的假突破反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityBreakReversal(BaseFactor):
    """计算近期ATR扩张比率与价格回调幅度，当波动率突然放大但价格未能持续时发出空头信号，反之多头信号。捕捉市场状态不明时的假突破反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volbreak",
            name="Volatility_Break_Reversal",
            display_name="波动突破反转",
            description="计算近期ATR扩张比率与价格回调幅度，当波动率突然放大但价格未能持续时发出空头信号，反之多头信号。捕捉市场状态不明时的假突破反转。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算ATR
        high, low, close = data['high'], data['low'], data['close']
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr_10 = tr.rolling(10).mean()
        atr_30 = tr.rolling(30).mean()
        # 波动率扩张比率
        vol_ratio = atr_10 / atr_30 - 1.0
        # 价格回调（相对于近期高点/低点）
        recent_high = high.rolling(10).max()
        recent_low = low.rolling(10).min()
        price_from_high = (close - recent_high) / (recent_high + 1e-8)
        price_from_low = (close - recent_low) / (recent_low + 1e-8)
        # 当波动率扩张且价格接近高点时看空，接近低点时看多
        vol_ratio_std = vol_ratio.rolling(20).std()
        zscore = (vol_ratio - vol_ratio.rolling(20).mean()) / (vol_ratio_std + 1e-8)
        signal = np.where(zscore > 1.5, -price_from_high, np.where(zscore < -1.5, -price_from_low, 0.0))
        # 归一化到[-1,1]
        result = signal.clip(-1, 1)
        return pd.Series(result, index=data.index, name="ai_gen_volbreak")
