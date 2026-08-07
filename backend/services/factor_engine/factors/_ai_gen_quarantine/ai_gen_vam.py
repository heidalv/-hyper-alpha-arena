"""AI因子: 波动率调整动量因子 | 置信:65% | 基于短期动量与近期波动率的比值，在波动率过高时削弱方向信号，避免在无序行情中追涨杀跌导致止损。计算20期收益率除以20期ATR（平均真实波幅）归一化值，并映射到[-1,1]区间。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentum(BaseFactor):
    """基于短期动量与近期波动率的比值，在波动率过高时削弱方向信号，避免在无序行情中追涨杀跌导致止损。计算20期收益率除以20期ATR（平均真实波幅）归一化值，并映射到[-1,1]区间。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vam",
            name="Volatility Adjusted Momentum",
            display_name="波动率调整动量因子",
            description="基于短期动量与近期波动率的比值，在波动率过高时削弱方向信号，避免在无序行情中追涨杀跌导致止损。计算20期收益率除以20期ATR（平均真实波幅）归一化值，并映射到[-1,1]区间。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算20期收益率
        ret = close.pct_change(20)
        # 计算ATR (20)
        tr = np.maximum(high - low, np.maximum(np.abs(high - close.shift(1)), np.abs(low - close.shift(1))))
        atr = tr.rolling(20).mean()
        # 归一化：收益率 / (ATR / close) 即收益率相对波动率比率
        norm = ret / (atr / close + 1e-10)
        # 截断并映射到[-1,1]
        clipped = np.clip(norm, -3, 3)
        result = pd.Series(clipped / 3.0, index=close.index)
        return result
