"""AI因子: 波动调整动量 | 置信:60% | 使用ATR标准化短期收益率，在低波动环境下减弱动量信号，避免在未知震荡行情中开仓。计算过去20日收益率除以过去20日平均ATR，然后映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentum(BaseFactor):
    """使用ATR标准化短期收益率，在低波动环境下减弱动量信号，避免在未知震荡行情中开仓。计算过去20日收益率除以过去20日平均ATR，然后映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_voladj_momentum",
            name="Volatility Adjusted Momentum",
            display_name="波动调整动量",
            description="使用ATR标准化短期收益率，在低波动环境下减弱动量信号，避免在未知震荡行情中开仓。计算过去20日收益率除以过去20日平均ATR，然后映射到[-1,1]。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        df = data.copy()
        # ATR
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift(1))
        low_close = np.abs(df['low'] - df['close'].shift(1))
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(20).mean()
        # 收益率
        ret = df['close'].pct_change(20)
        # 波动调整动量
        raw = ret / (atr / df['close'])  # 收益率除以相对ATR
        # 映射到[-1,1], 使用3倍标准差截断
        mean = raw.rolling(60).mean()
        std = raw.rolling(60).std()
        zscore = (raw - mean) / (std + 1e-8)
        clipped = np.clip(zscore, -3, 3) / 3.0
        result = pd.Series(clipped, index=df.index)
        return result.fillna(0.0)
