"""AI因子: 波动调整动量 | 置信:65% | 计算短期动量（20日收益率），并用ATR波动率进行缩放，在高波动环境下降低信号强度，避免追涨杀跌。最后通过Z-score标准化至[-1,1]区间。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Adjusted_Momentum(BaseFactor):
    """计算短期动量（20日收益率），并用ATR波动率进行缩放，在高波动环境下降低信号强度，避免追涨杀跌。最后通过Z-score标准化至[-1,1]区间。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vmnt",
            name="Volatility-Adjusted Momentum",
            display_name="波动调整动量",
            description="计算短期动量（20日收益率），并用ATR波动率进行缩放，在高波动环境下降低信号强度，避免追涨杀跌。最后通过Z-score标准化至[-1,1]区间。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        # ATR
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr = tr.rolling(14).mean()
        # 20日动量
        ret = (close / close.shift(20)) - 1
        # 波动率调整
        vol_ratio = atr / close
        vol_ratio = vol_ratio.replace(0, np.nan)
        adjusted = ret / vol_ratio
        # Z-score标准化
        z = (adjusted - adjusted.rolling(60).mean()) / adjusted.rolling(60).std()
        result = np.clip(z / 3.0, -1, 1)
        return result.fillna(0)
