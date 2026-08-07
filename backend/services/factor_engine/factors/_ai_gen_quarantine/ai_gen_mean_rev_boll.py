"""AI因子: 布林带均值回归因子 | 置信:65% | 当收盘价突破布林带上轨且RSI高于70时，发出看空信号（认为超买）；当收盘价跌破布林带下轨且RSI低于30时，发出看多信号（认为超卖）。中间状态线性映射至[-1,1]。该因子旨在捕捉短期反转机会，避免在趋势不明朗时追高（如亏损模式中的long止损）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Mean_Reversion_Bollinger_Band(BaseFactor):
    """当收盘价突破布林带上轨且RSI高于70时，发出看空信号（认为超买）；当收盘价跌破布林带下轨且RSI低于30时，发出看多信号（认为超卖）。中间状态线性映射至[-1,1]。该因子旨在捕捉短期反转机会，避免在趋势不明朗时追高（如亏损模式中的long止损）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mean_rev_boll",
            name="Mean Reversion Bollinger Band",
            display_name="布林带均值回归因子",
            description="当收盘价突破布林带上轨且RSI高于70时，发出看空信号（认为超买）；当收盘价跌破布林带下轨且RSI低于30时，发出看多信号（认为超卖）。中间状态线性映射至[-1,1]。该因子旨在捕捉短期反转机会，避免在趋势不明朗时追高（如亏损模式中的long止损）。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: DataFrame with columns ['open','high','low','close','volume']
        close = data['close']
        # Bollinger Bands (20,2)
        ma = close.rolling(20).mean()
        std = close.rolling(20).std()
        upper = ma + 2 * std
        lower = ma - 2 * std
        # RSI (14)
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        # Signal
        # Overbought: close > upper and rsi > 70 => short signal (-1)
        # Oversold: close < lower and rsi < 30 => long signal (+1)
        # Else linear interpolation
        raw = np.where((close > upper) & (rsi > 70), -1,
                       np.where((close < lower) & (rsi < 30), 1, 0))
        # Fill zeros with linear mapping based on distance from mean
        # Normalize distance to [-1,1] using tanh
        z_score = (close - ma) / (std + 1e-9)
        raw = np.where(raw == 0, -np.tanh(z_score / 2), raw)  # z_score positive -> negative signal
        # Ensure NaN handling
        result = pd.Series(raw, index=data.index).fillna(0).clip(-1,1)
        return result
