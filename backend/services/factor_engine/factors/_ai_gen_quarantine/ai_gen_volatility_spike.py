"""AI因子: 波动率异常反转因子 | 置信:60% | 捕捉波动率突然放大而价格方向不明的状态，容易导致逆势亏损。计算最近10根K线的ATR与过去50日ATR均值的比率，当比率超过阈值（如1.5）且当前价格处于近期区间中间位置时，表示无序波动，给出中性偏空信号。结合价格位置加权后映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilitySpikeReversalFactor(BaseFactor):
    """捕捉波动率突然放大而价格方向不明的状态，容易导致逆势亏损。计算最近10根K线的ATR与过去50日ATR均值的比率，当比率超过阈值（如1.5）且当前价格处于近期区间中间位置时，表示无序波动，给出中性偏空信号。结合价格位置加权后映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_spike",
            name="Volatility Spike Reversal Factor",
            display_name="波动率异常反转因子",
            description="捕捉波动率突然放大而价格方向不明的状态，容易导致逆势亏损。计算最近10根K线的ATR与过去50日ATR均值的比率，当比率超过阈值（如1.5）且当前价格处于近期区间中间位置时，表示无序波动，给出中性偏空信号。结合价格位置加权后映射到[-1,1]。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        # ATR
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr10 = tr.rolling(10).mean()
        atr50 = tr.rolling(50).mean()
        ratio = atr10 / atr50
        # 价格在近期区间中的位置
        rolling_high = high.rolling(20).max()
        rolling_low = low.rolling(20).min()
        pos = (close - rolling_low) / (rolling_high - rolling_low + 1e-10)
        # 信号：波动率突增且价格在中部（0.3~0.7）时，反向做空；极端位置不做
        spike = (ratio > 1.5).astype(float)
        mid = ((pos > 0.3) & (pos < 0.7)).astype(float)
        factor = -spike * mid * (ratio - 1.5) / 2.0  # 最大值约0.5，放大到1
        return factor.fillna(0).clip(-1, 1)
