"""AI因子: 波动率尖刺假突破 | 置信:60% | 当波动率（ATR）突然放大到近期高阈值，但价格涨幅却较小或为负，提示可能是假突破或短期反转，返回负值。反之当波动率正常且价格趋势良好时为正。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Spike_Fakeout(BaseFactor):
    """当波动率（ATR）突然放大到近期高阈值，但价格涨幅却较小或为负，提示可能是假突破或短期反转，返回负值。反之当波动率正常且价格趋势良好时为正。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_break",
            name="Volatility Spike Fakeout",
            display_name="波动率尖刺假突破",
            description="当波动率（ATR）突然放大到近期高阈值，但价格涨幅却较小或为负，提示可能是假突破或短期反转，返回负值。反之当波动率正常且价格趋势良好时为正。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        # ATR
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 波动率放大比率
        atr_ratio = atr / atr.shift(5) - 1
        # 价格变化（1日涨幅）
        ret = close.pct_change()
        # 当波动率放大超过30%但涨幅小于0.5%时，认为危险
        signal = np.where((atr_ratio > 0.3) & (ret < 0.005), -1,
                          np.where((atr_ratio < -0.2) & (ret > 0.01), 1, 0))
        result = pd.Series(signal, index=close.index).rolling(3).mean().fillna(0)
        return result.clip(-1,1)
