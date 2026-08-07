"""AI因子: 波动率突增均值回归 | 置信:55% | 检测短期内价格波动率急剧上升后的均值回归倾向。使用ATR比率（当前ATR相对过去均值的变化）和价格方向，当波动率暴增且价格偏离均线时，预期回归。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilitySurgeMeanReversion(BaseFactor):
    """检测短期内价格波动率急剧上升后的均值回归倾向。使用ATR比率（当前ATR相对过去均值的变化）和价格方向，当波动率暴增且价格偏离均线时，预期回归。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_surge",
            name="Volatility Surge Mean Reversion",
            display_name="波动率突增均值回归",
            description="检测短期内价格波动率急剧上升后的均值回归倾向。使用ATR比率（当前ATR相对过去均值的变化）和价格方向，当波动率暴增且价格偏离均线时，预期回归。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        period = 14
        surge_window = 20
        # ATR计算
        high_low = data['high'] - data['low']
        high_close = np.abs(data['high'] - data['close'].shift())
        low_close = np.abs(data['low'] - data['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        atr_ma = atr.rolling(surge_window).mean()
        atr_std = atr.rolling(surge_window).std()
        atr_z = (atr - atr_ma) / (atr_std + 1e-10)
        # 价格偏离均线
        mid = (data['high'] + data['low']) / 2
        sma = mid.rolling(20).mean()
        price_dev = (mid - sma) / sma
        # 信号：波动率暴增 + 价格偏移 -> 反向
        signal = np.where((atr_z > 2.0) & (price_dev > 0.02), -1, 0)
        signal = np.where((atr_z > 2.0) & (price_dev < -0.02), 1, signal)
        return pd.Series(signal, index=data.index)
