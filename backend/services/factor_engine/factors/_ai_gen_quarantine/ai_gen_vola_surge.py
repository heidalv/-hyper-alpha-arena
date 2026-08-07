"""AI因子: 波动率突变 | 置信:60% | 通过最近20日收益率标准差与过去60日均值比较，捕捉波动率突变。正值表示波动率急剧上升（风险增加），负值表示下降。用tanh缩放到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilitySurge(BaseFactor):
    """通过最近20日收益率标准差与过去60日均值比较，捕捉波动率突变。正值表示波动率急剧上升（风险增加），负值表示下降。用tanh缩放到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vola_surge",
            name="Volatility Surge",
            display_name="波动率突变",
            description="通过最近20日收益率标准差与过去60日均值比较，捕捉波动率突变。正值表示波动率急剧上升（风险增加），负值表示下降。用tanh缩放到[-1,1]。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        ret = data['close'].pct_change()
        # 近期波动率（20日滚动标准差）
        vol_short = ret.rolling(20).std()
        # 长期波动率均值（60日滚动均值，但这里用过去60日波动率的均值，避免前瞻）
        vol_long = vol_short.rolling(60).mean()
        # 波动率变化率
        surge = (vol_short - vol_long) / (vol_long + 1e-10)
        # 用tanh限制在[-1,1]
        result = np.tanh(surge * 5)  # 系数5增强敏感度
        return result
