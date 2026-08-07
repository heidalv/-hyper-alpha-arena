"""AI因子: 动量一致性 | 置信:55% | 计算短期（5日）和长期（20日）动量方向的一致性。当两者同向时为+1（正相关），反向时为-1（负相关），零表示不一致。可识别趋势是否明确，避免在方向混乱时入场。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Momentum_Coherence(BaseFactor):
    """计算短期（5日）和长期（20日）动量方向的一致性。当两者同向时为+1（正相关），反向时为-1（负相关），零表示不一致。可识别趋势是否明确，避免在方向混乱时入场。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_coherence",
            name="Momentum Coherence",
            display_name="动量一致性",
            description="计算短期（5日）和长期（20日）动量方向的一致性。当两者同向时为+1（正相关），反向时为-1（负相关），零表示不一致。可识别趋势是否明确，避免在方向混乱时入场。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        mom_short = close.pct_change(5)
        mom_long = close.pct_change(20)
        # 方向符号
        sign_short = np.sign(mom_short)
        sign_long = np.sign(mom_long)
        # 一致性：同向则+1，反向则-1，一方为零则0
        coherence = sign_short * sign_long
        # 用动量强度加权？简单版本
        # 若一方很小，降低置信度
        strength = (mom_short.abs() + mom_long.abs()) / 2
        result = coherence * (strength / strength.rolling(60).max().replace(0, np.nan)).fillna(0)
        return result.fillna(0).clip(-1, 1)
