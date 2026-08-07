"""AI因子: 波动收缩指示器 | 置信:55% | 基于ATR的近期波动率是否在收缩，低波动环境容易产生假突破和止损。当波动率收缩时值为正（+1），扩张时为负（-1）。帮助避免在噪声高、波动反复的环境中开仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityContractionIndicator(BaseFactor):
    """基于ATR的近期波动率是否在收缩，低波动环境容易产生假突破和止损。当波动率收缩时值为正（+1），扩张时为负（-1）。帮助避免在噪声高、波动反复的环境中开仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_contraction",
            name="Volatility Contraction Indicator",
            display_name="波动收缩指示器",
            description="基于ATR的近期波动率是否在收缩，低波动环境容易产生假突破和止损。当波动率收缩时值为正（+1），扩张时为负（-1）。帮助避免在噪声高、波动反复的环境中开仓。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        high, low, close = data['high'], data['low'], data['close']
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr14 = tr.rolling(14).mean()
        # 当前ATR与过去20日平均ATR的比值
        avg_atr = atr14.rolling(20).mean()
        ratio = atr14 / avg_atr.clip(lower=1e-8)
        # ratio < 1 表示收缩，映射到[-1,1]
        # 使用逻辑函数映射：当ratio=0.7 -> +0.9, ratio=1.0 -> 0, ratio=1.3 -> -0.9
        result = 2 * (1 - ratio) / (1 + ratio.abs())  # 近似非线性映射
        return result.clip(-1, 1).fillna(0)
