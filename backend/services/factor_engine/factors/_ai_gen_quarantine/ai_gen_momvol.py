"""AI因子: 动量波动比 | 置信:60% | 计算短期收益率与同期波动率的比值，当动量很弱但波动率很高时，趋势不可靠，市场处于未知状态，应反向做空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Momentum_to_Volatility_Ratio(BaseFactor):
    """计算短期收益率与同期波动率的比值，当动量很弱但波动率很高时，趋势不可靠，市场处于未知状态，应反向做空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momvol",
            name="Momentum-to-Volatility Ratio",
            display_name="动量波动比",
            description="计算短期收益率与同期波动率的比值，当动量很弱但波动率很高时，趋势不可靠，市场处于未知状态，应反向做空。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ret = close.pct_change(5)
        vol = close.pct_change().rolling(20).std()
        ratio = ret / (vol + 1e-10)
        result = -np.clip(ratio, -1, 1)
        return result
