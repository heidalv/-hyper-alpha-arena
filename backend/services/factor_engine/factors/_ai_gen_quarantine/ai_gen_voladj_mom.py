"""AI因子: 波动率调整动量 | 置信:60% | 计算过去N周期收益率除以波动率，当波动率较低且动量为正时看多，反之看空，以规避高波动未知市场下的假突破"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Adjusted_Momentum(BaseFactor):
    """计算过去N周期收益率除以波动率，当波动率较低且动量为正时看多，反之看空，以规避高波动未知市场下的假突破"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_voladj_mom",
            name="Volatility-Adjusted Momentum",
            display_name="波动率调整动量",
            description="计算过去N周期收益率除以波动率，当波动率较低且动量为正时看多，反之看空，以规避高波动未知市场下的假突破",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: pd.DataFrame with columns ['open','high','low','close','volume']
        import numpy as np
        period = 20
        ret = data['close'].pct_change(period)
        vol = data['close'].pct_change().rolling(period).std()
        vol_adj = ret / (vol + 1e-8)
        # 标准化到[-1,1]
        z = (vol_adj - vol_adj.rolling(120).mean()) / (vol_adj.rolling(120).std() + 1e-8)
        result = np.clip(z, -1, 1)
        return result.fillna(0)
