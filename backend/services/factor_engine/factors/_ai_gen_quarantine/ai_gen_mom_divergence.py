"""AI因子: 短长动量背离 | 置信:60% | 当短期动量（如5日）与长期动量（如50日）方向相反且绝对值均超过阈值时，预示趋势衰竭可能反转。规避regime=unknown下的震荡行情，捕捉高确信度反转点。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortTermVsLongTermMomentumDivergence(BaseFactor):
    """当短期动量（如5日）与长期动量（如50日）方向相反且绝对值均超过阈值时，预示趋势衰竭可能反转。规避regime=unknown下的震荡行情，捕捉高确信度反转点。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_divergence",
            name="Short-term vs Long-term Momentum Divergence",
            display_name="短长动量背离",
            description="当短期动量（如5日）与长期动量（如50日）方向相反且绝对值均超过阈值时，预示趋势衰竭可能反转。规避regime=unknown下的震荡行情，捕捉高确信度反转点。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        # 短期动量：5日收益率
        mom_short = close.pct_change(5)
        # 长期动量：50日收益率
        mom_long = close.pct_change(50)
        # 标准化为z-score（滚动20日）
        z_short = (mom_short - mom_short.rolling(20).mean()) / (mom_short.rolling(20).std() + 1e-10)
        z_long = (mom_long - mom_long.rolling(20).mean()) / (mom_long.rolling(20).std() + 1e-10)
        # 背离条件：短期和长期符号相反，且绝对值均大于1.5
        diverge_up = (z_short < -1.5) & (z_long > 1.5)  # 短期超卖，长期超买 -> 看涨
        diverge_down = (z_short > 1.5) & (z_long < -1.5)  # 短期超买，长期超卖 -> 看跌
        signal = np.where(diverge_up, 1.0, np.where(diverge_down, -1.0, 0.0))
        return pd.Series(signal, index=data.index).fillna(0)
