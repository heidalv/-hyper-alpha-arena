"""AI因子: 波动率调整短期反转 | 置信:60% | 以20日已实现波动率对5日短期收益率进行标准化，得到波动率调整后的短期反转信号。当短期涨幅显著超过其波动率水平时因子值为正（超买），反之为负（超卖）。该因子在波动率聚集环境下能更稳健地识别均值回归机会，避免高波动品种的极端值主导。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityScaledShortReversal(BaseFactor):
    """以20日已实现波动率对5日短期收益率进行标准化，得到波动率调整后的短期反转信号。当短期涨幅显著超过其波动率水平时因子值为正（超买），反之为负（超卖）。该因子在波动率聚集环境下能更稳健地识别均值回归机会，避免高波动品种的极端值主导。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_scaled_reversal",
            name="Volatility Scaled Short Reversal",
            display_name="波动率调整短期反转",
            description="以20日已实现波动率对5日短期收益率进行标准化，得到波动率调整后的短期反转信号。当短期涨幅显著超过其波动率水平时因子值为正（超买），反之为负（超卖）。该因子在波动率聚集环境下能更稳健地识别均值回归机会，避免高波动品种的极端值主导。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret5 = data['close'].pct_change(5)
        vol20 = data['close'].pct_change().rolling(20).std()
        result = (-ret5 / (vol20 + 1e-9)).clip(-1, 1)
        return result
