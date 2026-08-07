"""AI因子: 波动率分化因子 | 置信:60% | 通过短期波动率与中期波动率的比值，捕捉市场状态变化。当短期波动率显著高于中期波动率时，表明市场进入未知状态，可能不适合交易，因子偏向负值；反之则偏向正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityDivergence(BaseFactor):
    """通过短期波动率与中期波动率的比值，捕捉市场状态变化。当短期波动率显著高于中期波动率时，表明市场进入未知状态，可能不适合交易，因子偏向负值；反之则偏向正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_div",
            name="Volatility Divergence",
            display_name="波动率分化因子",
            description="通过短期波动率与中期波动率的比值，捕捉市场状态变化。当短期波动率显著高于中期波动率时，表明市场进入未知状态，可能不适合交易，因子偏向负值；反之则偏向正值。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算短期（5日）和中期（20日）波动率（标准差）
        short_vol = data['close'].pct_change().rolling(5).std()
        medium_vol = data['close'].pct_change().rolling(20).std()
        # 避免除以零
        ratio = short_vol / (medium_vol + 1e-10)
        # 标准化到[-1,1]，以1为中性
        ratio = ratio - 1
        # 使用tanh限幅
        result = np.tanh(ratio * 3)
        return result.fillna(0).clip(-1, 1).astype(float)
