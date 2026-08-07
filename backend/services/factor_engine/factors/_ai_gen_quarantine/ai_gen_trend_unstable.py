"""AI因子: 趋势不稳定指数 | 置信:65% | 通过短期价格序列的单调性判断趋势是否稳定。当价格频繁穿越短期均线或ATR突然扩大时，趋势不稳定，容易触发止损。该因子输出负值表示应避免顺趋势操作。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendInstabilityIndex(BaseFactor):
    """通过短期价格序列的单调性判断趋势是否稳定。当价格频繁穿越短期均线或ATR突然扩大时，趋势不稳定，容易触发止损。该因子输出负值表示应避免顺趋势操作。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_unstable",
            name="Trend Instability Index",
            display_name="趋势不稳定指数",
            description="通过短期价格序列的单调性判断趋势是否稳定。当价格频繁穿越短期均线或ATR突然扩大时，趋势不稳定，容易触发止损。该因子输出负值表示应避免顺趋势操作。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
    
        # 计算ATR
        tr = pd.concat([high - low,
                       abs(high - close.shift(1)),
                       abs(low - close.shift(1))], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        atr_ratio = atr / close.shift(1).replace(0, 1)
    
        # 短期均线 (5周期EMA)
        ema5 = close.ewm(span=5, adjust=False).mean()
    
        # 价格穿越均线的次数 (过去10根K线)
        cross = ((close.shift(1) > ema5.shift(1)) & (close < ema5)) | \
                ((close.shift(1) < ema5.shift(1)) & (close > ema5))
        cross_count = cross.rolling(10).sum().fillna(0)
    
        # 趋势不稳定: 高穿越次数 或 ATR突然飙升 (相对于20日均值)
        atr_ma = atr_ratio.rolling(20).mean().fillna(0)
        atr_surge = atr_ratio > (atr_ma * 2)
    
        # 综合得分: 穿越次数 > 3 或 ATR飙升 -> 不稳定, 输出负值
        unstable = ((cross_count > 3) | atr_surge).astype(float)
        # 强度: 穿越次数越多越不稳定
        strength = cross_count / 10.0  # 0~1
        signal = -np.maximum(strength, unstable * 0.5)
    
        result = pd.Series(signal, index=close.index).clip(-1, 1)
        return result
