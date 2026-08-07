"""AI因子: 波动率偏度异常因子 | 置信:60% | 通过计算近期收益率的偏度与波动率的关系，识别极端价格行为。当偏度绝对值大且波动率异常时，市场状态模糊（regime=unknown），因子输出负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilitySkewAnomaly(BaseFactor):
    """通过计算近期收益率的偏度与波动率的关系，识别极端价格行为。当偏度绝对值大且波动率异常时，市场状态模糊（regime=unknown），因子输出负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_skew",
            name="Volatility Skew Anomaly",
            display_name="波动率偏度异常因子",
            description="通过计算近期收益率的偏度与波动率的关系，识别极端价格行为。当偏度绝对值大且波动率异常时，市场状态模糊（regime=unknown），因子输出负值。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # Daily returns
        returns = data['close'].pct_change()
        # Rolling skewness (20 periods)
        skew = returns.rolling(20).skew()
        # Rolling volatility (20 periods std)
        vol = returns.rolling(20).std()
        # Normalize skew and vol using z-score over longer window
        skew_z = (skew - skew.rolling(60).mean()) / skew.rolling(60).std()
        vol_z = (vol - vol.rolling(60).mean()) / vol.rolling(60).std()
        # Factor: high absolute skew combined with high vol indicates regime uncertainty
        factor = -np.abs(skew_z) * vol_z
        # Clip to [-1,1]
        factor = factor.clip(-1, 1)
        return factor.fillna(0)
