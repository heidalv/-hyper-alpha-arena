"""AI因子: 流动性质量因子 | 置信:60% | 基于Amihud非流动性指标，衡量市场流动性质量。流动性恶化时因子转负，可能加剧滑点和小额清理风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityQuality(BaseFactor):
    """基于Amihud非流动性指标，衡量市场流动性质量。流动性恶化时因子转负，可能加剧滑点和小额清理风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liq_quality",
            name="liquidity_quality",
            display_name="流动性质量因子",
            description="基于Amihud非流动性指标，衡量市场流动性质量。流动性恶化时因子转负，可能加剧滑点和小额清理风险。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        df = data.copy()
        close = df['close']
        volume = df['volume']
        ret = close.pct_change()
        illiq = ret.abs() / volume.replace(0, np.nan)
        illiq_ma = illiq.rolling(20).mean()
        mean_illiq = illiq_ma.rolling(60).mean()
        std_illiq = illiq_ma.rolling(60).std()
        z = (illiq_ma - mean_illiq) / std_illiq
        factor = -np.tanh(z)
        return factor.fillna(0)
