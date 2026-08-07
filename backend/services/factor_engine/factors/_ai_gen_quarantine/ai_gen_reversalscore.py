"""AI因子: 反转风险评分 | 置信:60% | 基于短期波动率与长期波动率的比值以及价格相对于均线的偏离程度，评估当前是否处于高反转风险状态。当短期波动远大于长期波动且价格偏离均线较远时，得分接近-1（警告反转）；反之接近+1表示低风险。适用于识别regime unknown下的陷阱。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReversalRiskScore(BaseFactor):
    """基于短期波动率与长期波动率的比值以及价格相对于均线的偏离程度，评估当前是否处于高反转风险状态。当短期波动远大于长期波动且价格偏离均线较远时，得分接近-1（警告反转）；反之接近+1表示低风险。适用于识别regime unknown下的陷阱。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reversalscore",
            name="Reversal Risk Score",
            display_name="反转风险评分",
            description="基于短期波动率与长期波动率的比值以及价格相对于均线的偏离程度，评估当前是否处于高反转风险状态。当短期波动远大于长期波动且价格偏离均线较远时，得分接近-1（警告反转）；反之接近+1表示低风险。适用于识别regime unknown下的陷阱。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        short_vol = close.rolling(5).std()
        long_vol = close.rolling(20).std()
        vol_ratio = short_vol / long_vol
        # 避免除零
        vol_ratio = vol_ratio.replace([np.inf, -np.inf], 1.0).fillna(1.0)
        ma20 = close.rolling(20).mean()
        deviation = (close - ma20) / ma20
        # 综合：当vol_ratio高且deviation绝对值大时，反转风险高
        risk = -np.abs(deviation) * (vol_ratio - 1)
        # 映射到[-1,1]：用tanh压缩
        result = np.tanh(risk * 10)  # 适当缩放
        return result.fillna(0.0).clip(-1, 1)
