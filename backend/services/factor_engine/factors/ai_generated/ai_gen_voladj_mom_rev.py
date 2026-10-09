"""AI因子: 波动率调整动量反转 | 置信:60% | 用20期收益率除以同期已实现波动率得到风险调整动量，再取负号捕捉超买超卖后的均值回归。波动率调整使因子在不同币种间可比，负号体现短期反转逻辑，适合当前regime=unknown的高波动环境。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentumReversal(BaseFactor):
    """用20期收益率除以同期已实现波动率得到风险调整动量，再取负号捕捉超买超卖后的均值回归。波动率调整使因子在不同币种间可比，负号体现短期反转逻辑，适合当前regime=unknown的高波动环境。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_voladj_mom_rev",
            name="Volatility Adjusted Momentum Reversal",
            display_name="波动率调整动量反转",
            description="用20期收益率除以同期已实现波动率得到风险调整动量，再取负号捕捉超买超卖后的均值回归。波动率调整使因子在不同币种间可比，负号体现短期反转逻辑，适合当前regime=unknown的高波动环境。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        result = (-ret / (vol + 1e-9)).clip(-1, 1)
        return result
