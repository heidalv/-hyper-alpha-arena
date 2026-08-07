"""AI因子: K线形态反转因子 | 置信:60% | 识别常见的反转K线形态（如锤子线、射击之星、吞没形态），尤其关注长上影线和长下影线。当出现长上影线（上影线长度是实体的2倍以上）且收盘价接近当日最低时，给出做空信号；长下影线反之。信号强度与影线相对长度成比例，映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class CandlePatternReversalFactor(BaseFactor):
    """识别常见的反转K线形态（如锤子线、射击之星、吞没形态），尤其关注长上影线和长下影线。当出现长上影线（上影线长度是实体的2倍以上）且收盘价接近当日最低时，给出做空信号；长下影线反之。信号强度与影线相对长度成比例，映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_candle_reversal",
            name="Candle Pattern Reversal Factor",
            display_name="K线形态反转因子",
            description="识别常见的反转K线形态（如锤子线、射击之星、吞没形态），尤其关注长上影线和长下影线。当出现长上影线（上影线长度是实体的2倍以上）且收盘价接近当日最低时，给出做空信号；长下影线反之。信号强度与影线相对长度成比例，映射到[-1,1]。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        open = data['open']
        high = data['high']
        low = data['low']
        close = data['close']
        body = (close - open).abs()
        upper_shadow = high - close.where(close >= open, open)
        lower_shadow = close.where(close <= open, open) - low
        # 长上影线做空条件：上影线 > 2倍实体，且收盘在低半区
        short_cond = (upper_shadow > 2 * body) & (close < (high + low) / 2)
        # 长下影线做多条件：下影线 > 2倍实体，且收盘在高半区
        long_cond = (lower_shadow > 2 * body) & (close > (high + low) / 2)
        # 信号强度：影线/ATR归一化
        atr = (high - low).rolling(14).mean()
        short_signal = -short_cond * (upper_shadow / (atr + 1e-10)).clip(0, 2) / 2.0
        long_signal = long_cond * (lower_shadow / (atr + 1e-10)).clip(0, 2) / 2.0
        factor = short_signal + long_signal
        return factor.fillna(0).clip(-1, 1)
