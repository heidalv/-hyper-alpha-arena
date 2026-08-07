"""AI因子: 多周期动量一致性 | 置信:70% | 比较短期（5日）与长期（20日）动量方向是否一致，不一致表示趋势不明确（unknown regime），输出-1（不一致/unknown）、0（中性）、1（一致/趋势明确）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiTimeframeMomentumConsistency(BaseFactor):
    """比较短期（5日）与长期（20日）动量方向是否一致，不一致表示趋势不明确（unknown regime），输出-1（不一致/unknown）、0（中性）、1（一致/趋势明确）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_consistency",
            name="Multi-Timeframe Momentum Consistency",
            display_name="多周期动量一致性",
            description="比较短期（5日）与长期（20日）动量方向是否一致，不一致表示趋势不明确（unknown regime），输出-1（不一致/unknown）、0（中性）、1（一致/趋势明确）。",
            category="behavioral",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        # 短期动量：5日变化率
        mom_short = close.pct_change(5)
        # 长期动量：20日变化率
        mom_long = close.pct_change(20)
        # 方向符号
        sign_short = np.sign(mom_short)
        sign_long = np.sign(mom_long)
        # 一致性：相同符号为1，相反为-1，零为0
        result = pd.Series(0.0, index=data.index)
        consistent = (sign_short == sign_long) & (sign_short != 0) & (sign_long != 0)
        inconsistent = (sign_short != sign_long) & (sign_short != 0) & (sign_long != 0)
        result[consistent] = 1.0
        result[inconsistent] = -1.0
        # 当有任意一个为零时，保持0（中性）
        return result
