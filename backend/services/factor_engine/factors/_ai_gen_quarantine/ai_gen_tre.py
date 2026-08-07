"""AI因子: 趋势衰竭反转 | 置信:70% | 当价格达到布林带极端且ADX趋势强度回落时，发出反向信号。旨在捕捉max_hold_timeout亏损中常见的趋势末端反转。正值看多，负值看空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendExhaustionReversal(BaseFactor):
    """当价格达到布林带极端且ADX趋势强度回落时，发出反向信号。旨在捕捉max_hold_timeout亏损中常见的趋势末端反转。正值看多，负值看空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tre",
            name="Trend Exhaustion Reversal",
            display_name="趋势衰竭反转",
            description="当价格达到布林带极端且ADX趋势强度回落时，发出反向信号。旨在捕捉max_hold_timeout亏损中常见的趋势末端反转。正值看多，负值看空。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 布林带 (20, 2)
        ma = close.rolling(20).mean()
        std = close.rolling(20).std()
        upper = ma + 2 * std
        lower = ma - 2 * std
        bb_position = (close - ma) / (upper - lower + 1e-9)  # 介于 -0.5 到 0.5 左右
        # ADX 趋势强度 (14)
        tr = np.maximum(high - low, np.abs(high - close.shift()), np.abs(low - close.shift()))
        atr = tr.rolling(14).mean()
        up = high - high.shift()
        down = low.shift() - low
        plus_dm = np.where((up > down) & (up > 0), up, 0.0)
        minus_dm = np.where((down > up) & (down > 0), down, 0.0)
        plus_di = 100 * pd.Series(plus_dm, index=data.index).rolling(14).mean() / atr
        minus_di = 100 * pd.Series(minus_dm, index=data.index).rolling(14).mean() / atr
        dx = np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)
        adx = dx.rolling(14).mean()
        adx_change = adx.diff(3)
        # 信号：价格在上轨上方且ADX下降 -> 看空；价格在下轨下方且ADX下降 -> 看多
        signal = np.where((close > upper) & (adx_change < 0), -1.0,
                          np.where((close < lower) & (adx_change < 0), 1.0, 0.0))
        # 平滑并限制在[-1,1]
        result = pd.Series(signal, index=data.index).rolling(2).mean().clip(-1, 1)
        return result.fillna(0)
