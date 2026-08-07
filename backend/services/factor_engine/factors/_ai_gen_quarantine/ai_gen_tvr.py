"""AI因子: 趋势波动率比 | 置信:70% | 计算过去N周期内价格变化方向一致性与波动率的比值，用于识别强势趋势与震荡市。当趋势方向明确且波动率低时值为正，反之负值表示市场噪音大。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendVolatilityRatio(BaseFactor):
    """计算过去N周期内价格变化方向一致性与波动率的比值，用于识别强势趋势与震荡市。当趋势方向明确且波动率低时值为正，反之负值表示市场噪音大。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tvr",
            name="Trend_Volatility_Ratio",
            display_name="趋势波动率比",
            description="计算过去N周期内价格变化方向一致性与波动率的比值，用于识别强势趋势与震荡市。当趋势方向明确且波动率低时值为正，反之负值表示市场噪音大。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        period = 20
        # 简单动量方向一致性
        returns = data['close'].pct_change()
        direction = np.sign(returns)
        # 过去period天方向的和，衡量一致性
        dir_sum = direction.rolling(period).sum()
        # 波动率用ATR标准化
        high_low = data['high'] - data['low']
        high_close = np.abs(data['high'] - data['close'].shift())
        low_close = np.abs(data['low'] - data['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        # 价格变化率
        price_change = data['close'].pct_change(period)
        # 比值，方向一致性 * 价格变化幅度 / 波动率
        ratio = (dir_sum / period) * (price_change / (atr / data['close'].shift(period)))
        # 缩放到[-1,1]
        result = np.clip(ratio, -1, 1)
        return result
