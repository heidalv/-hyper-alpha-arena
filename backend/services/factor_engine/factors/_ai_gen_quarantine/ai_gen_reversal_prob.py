"""AI因子: 成交量确认均值回归因子 | 置信:62% | 价格远离移动均线且成交量异常放大时，表明市场情绪极端，容易发生反转。计算价格与N日均线的标准化距离，乘以成交量相对于均线的偏离，并取负号以捕捉回归机会。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MeanReversionWithVolumeConfirmation(BaseFactor):
    """价格远离移动均线且成交量异常放大时，表明市场情绪极端，容易发生反转。计算价格与N日均线的标准化距离，乘以成交量相对于均线的偏离，并取负号以捕捉回归机会。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reversal_prob",
            name="Mean Reversion with Volume Confirmation",
            display_name="成交量确认均值回归因子",
            description="价格远离移动均线且成交量异常放大时，表明市场情绪极端，容易发生反转。计算价格与N日均线的标准化距离，乘以成交量相对于均线的偏离，并取负号以捕捉回归机会。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        N = 30
        ma = data['close'].rolling(N).mean()
        std = data['close'].rolling(N).std()
        zscore = (data['close'] - ma) / (std + 1e-10)
        zscore_clipped = np.clip(zscore, -3, 3) / 3
        vol_ma = data['volume'].rolling(N).mean()
        vol_dev = (data['volume'] - vol_ma) / (vol_ma + 1e-10)
        vol_dev_clipped = np.clip(vol_dev, -1, 1)
        factor = -zscore_clipped * vol_dev_clipped
        return factor.fillna(0)
