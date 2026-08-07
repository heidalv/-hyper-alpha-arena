"""AI因子: 波动率调整动量 | 置信:60% | 计算过去N周期价格变化率，然后用同期波动率（标准差）进行归一化。在低波动环境下动量更可靠，高波动时信号被压缩，以此避免在未知剧烈震荡中开仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class vol_adjusted_momentum(BaseFactor):
    """计算过去N周期价格变化率，然后用同期波动率（标准差）进行归一化。在低波动环境下动量更可靠，高波动时信号被压缩，以此避免在未知剧烈震荡中开仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_adj_momentum",
            name="vol_adjusted_momentum",
            display_name="波动率调整动量",
            description="计算过去N周期价格变化率，然后用同期波动率（标准差）进行归一化。在低波动环境下动量更可靠，高波动时信号被压缩，以此避免在未知剧烈震荡中开仓。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        ret = data['close'].pct_change(12)
        vol = data['close'].pct_change().rolling(20).std()
        vol_norm = vol / vol.rolling(60).mean().clip(lower=1e-10)
        # 高波动时衰减信号
        signal = ret / (vol_norm + 0.5)
        return signal.clip(-1, 1)
