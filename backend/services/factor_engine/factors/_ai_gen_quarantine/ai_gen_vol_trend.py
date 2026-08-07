"""AI因子: 波动率调整趋势强度因子 | 置信:60% | 趋势方向强度容易被极端波动干扰。使用ATR标准化价格变化幅度，结合成交量确认趋势。当趋势方向与成交量同向时赋予正值，反向时赋予负值，且幅度受波动率抑制。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedTrendStrength(BaseFactor):
    """趋势方向强度容易被极端波动干扰。使用ATR标准化价格变化幅度，结合成交量确认趋势。当趋势方向与成交量同向时赋予正值，反向时赋予负值，且幅度受波动率抑制。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_trend",
            name="Volatility-Adjusted Trend Strength",
            display_name="波动率调整趋势强度因子",
            description="趋势方向强度容易被极端波动干扰。使用ATR标准化价格变化幅度，结合成交量确认趋势。当趋势方向与成交量同向时赋予正值，反向时赋予负值，且幅度受波动率抑制。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        period = 14
        tr = np.maximum(data['high'] - data['low'], np.abs(data['high'] - data['close'].shift(1)), np.abs(data['low'] - data['close'].shift(1)))
        atr = tr.rolling(period).mean()
        close_change = data['close'].diff(period)
        vol_ratio = data['volume'] / data['volume'].rolling(period).mean()
        trend_signal = close_change / (atr + 1e-10)
        trend_signal = np.clip(trend_signal, -3, 3) / 3
        vol_factor = np.clip(vol_ratio - 1, -1, 1)
        strength = trend_signal * vol_factor
        return strength.fillna(0)
