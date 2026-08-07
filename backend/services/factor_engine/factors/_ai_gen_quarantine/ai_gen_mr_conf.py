"""AI因子: 均值回归置信度因子 | 置信:55% | 结合RSI超买超卖、布林带位置与成交量确认，判断当前是否适合均值回归操作。RSI<30且价格跌破下轨且缩量时因子=+1（做多回归），RSI>70且价格突破上轨且放量时因子=-1（做空回归），其他为0。用于避免在趋势延续中反向操作。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MeanReversionConfidence(BaseFactor):
    """结合RSI超买超卖、布林带位置与成交量确认，判断当前是否适合均值回归操作。RSI<30且价格跌破下轨且缩量时因子=+1（做多回归），RSI>70且价格突破上轨且放量时因子=-1（做空回归），其他为0。用于避免在趋势延续中反向操作。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mr_conf",
            name="Mean_Reversion_Confidence",
            display_name="均值回归置信度因子",
            description="结合RSI超买超卖、布林带位置与成交量确认，判断当前是否适合均值回归操作。RSI<30且价格跌破下轨且缩量时因子=+1（做多回归），RSI>70且价格突破上轨且放量时因子=-1（做空回归），其他为0。用于避免在趋势延续中反向操作。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
        n = 20
        # RSI
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.rolling(n).mean()
        avg_loss = loss.rolling(n).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        # Bollinger Bands
        sma = close.rolling(n).mean()
        std = close.rolling(n).std()
        upper = sma + 2 * std
        lower = sma - 2 * std
        # Volume condition: compare to median volume
        vol_median = volume.rolling(50).median()
        vol_ratio = volume / vol_median
        result = pd.Series(0.0, index=data.index)
        # long setup: RSI<30, close<lower, volume low (vol_ratio<0.8)
        long_cond = (rsi < 30) & (close < lower) & (vol_ratio < 0.8)
        # short setup: RSI>70, close>upper, volume high (vol_ratio>1.2)
        short_cond = (rsi > 70) & (close > upper) & (vol_ratio > 1.2)
        result[long_cond] = 1.0
        result[short_cond] = -1.0
        return result.fillna(0.0)
