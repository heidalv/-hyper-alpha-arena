"""AI因子: 波动率压缩 | 置信:70% | 使用短期ATR(5)与长期ATR(20)的差异归一化，公式为 (short_atr - long_atr) / (short_atr + long_atr)。正值表示短期波动大于长期（趋势扩张），负值表示短期波动小于长期（震荡压缩）。在regime=unknown时该因子通常为负，提示避免做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Compression(BaseFactor):
    """使用短期ATR(5)与长期ATR(20)的差异归一化，公式为 (short_atr - long_atr) / (short_atr + long_atr)。正值表示短期波动大于长期（趋势扩张），负值表示短期波动小于长期（震荡压缩）。在regime=unknown时该因子通常为负，提示避免做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_compression",
            name="Volatility Compression",
            display_name="波动率压缩",
            description="使用短期ATR(5)与长期ATR(20)的差异归一化，公式为 (short_atr - long_atr) / (short_atr + long_atr)。正值表示短期波动大于长期（趋势扩张），负值表示短期波动小于长期（震荡压缩）。在regime=unknown时该因子通常为负，提示避免做多。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        def true_range(high, low, close):
            prev_close = close.shift(1)
            tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
            return tr
        tr = true_range(data['high'], data['low'], data['close'])
        short_atr = tr.rolling(5).mean()
        long_atr = tr.rolling(20).mean()
        # 避免除零
        denom = short_atr + long_atr
        denom = denom.replace(0, np.nan)
        result = (short_atr - long_atr) / denom
        result = result.fillna(0).clip(-1,1)
        return result
