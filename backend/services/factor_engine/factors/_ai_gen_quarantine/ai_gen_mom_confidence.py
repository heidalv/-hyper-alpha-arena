"""AI因子: 动量置信度因子 | 置信:70% | 结合近期动量强度和波动率，当动量强且波动率低时置信度高，因子偏向正值；当动量弱或波动率高时置信度低，因子偏向负值，用于规避regime=unknown的高波动环境。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumConfidence(BaseFactor):
    """结合近期动量强度和波动率，当动量强且波动率低时置信度高，因子偏向正值；当动量弱或波动率高时置信度低，因子偏向负值，用于规避regime=unknown的高波动环境。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_confidence",
            name="Momentum Confidence",
            display_name="动量置信度因子",
            description="结合近期动量强度和波动率，当动量强且波动率低时置信度高，因子偏向正值；当动量弱或波动率高时置信度低，因子偏向负值，用于规避regime=unknown的高波动环境。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算10日动量（收益率）
        mom = data['close'].pct_change(10)
        # 计算20日波动率
        vol = data['close'].pct_change().rolling(20).std()
        # 归一化波动率（使用滚动排名）
        vol_rank = vol.rank(pct=True)
        # 动量置信度 = mom * (1 - vol_rank) 并投影到[-1,1]
        raw = mom * (1 - vol_rank)
        result = np.tanh(raw * 5)
        return result.fillna(0).clip(-1, 1).astype(float)
