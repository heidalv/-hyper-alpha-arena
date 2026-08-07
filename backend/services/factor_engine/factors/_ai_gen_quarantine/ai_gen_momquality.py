"""AI因子: 动量质量 | 置信:50% | 衡量近期动量的质量：使用连续收益的符号一致性（价格走势的肌肉记忆）与波动调整后动量强度的乘积。当动量质量低时，价格走势不连贯，容易发生反转导致亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Momentum_Quality(BaseFactor):
    """衡量近期动量的质量：使用连续收益的符号一致性（价格走势的肌肉记忆）与波动调整后动量强度的乘积。当动量质量低时，价格走势不连贯，容易发生反转导致亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momquality",
            name="Momentum_Quality",
            display_name="动量质量",
            description="衡量近期动量的质量：使用连续收益的符号一致性（价格走势的肌肉记忆）与波动调整后动量强度的乘积。当动量质量低时，价格走势不连贯，容易发生反转导致亏损。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']

        # 计算5日收益率
        ret5 = close.pct_change(5)
        # 计算日收益率符号的一致性：过去5天内正收益天数的比例
        daily_ret = close.pct_change()
        pos_days = (daily_ret > 0).rolling(5).sum()
        consistency = pos_days / 5.0  # 0~1

        # 波动调整动量强度：ret5 / 波动率（20日标准差）
        vol = daily_ret.rolling(20).std()
        momentum_strength = ret5 / (vol + 1e-10)

        # 动量质量 = 一致性 * 动量强度，然后缩放到[-1,1]
        raw = (consistency - 0.5) * 2 * momentum_strength  # 一致性差异[-1,1]乘动量强度
        # 用tanh限制     result = pd.Series(np.tanh(raw), index=data.index)
        return result
