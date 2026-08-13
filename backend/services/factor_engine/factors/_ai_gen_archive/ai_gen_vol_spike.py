"""AI因子: 波动率尖峰因子 | 置信:60% | 捕捉价格剧烈波动风险，ATR相对历史均值的偏离越大，因子越接近±1。高波动率环境可能导致止损/止盈被意外触发。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilitySpike(BaseFactor):
    """捕捉价格剧烈波动风险，ATR相对历史均值的偏离越大，因子越接近±1。高波动率环境可能导致止损/止盈被意外触发。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_spike",
            name="volatility_spike",
            display_name="波动率尖峰因子",
            description="捕捉价格剧烈波动风险，ATR相对历史均值的偏离越大，因子越接近±1。高波动率环境可能导致止损/止盈被意外触发。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        df = data.copy()
        high, low, close = df['high'], df['low'], df['close']
        prev_close = close.shift(1)
        tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        atr_ma = atr.rolling(30).mean()
        ratio = atr / atr_ma - 1
        factor = np.tanh(ratio * 10)
        return factor.fillna(0)
