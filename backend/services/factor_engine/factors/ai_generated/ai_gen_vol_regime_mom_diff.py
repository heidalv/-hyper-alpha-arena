"""AI因子: 波动率状态动量差 | 置信:60% | 用短长周期收益率差(5日-20日)刻画动量加速度，除以20日已实现波动率进行风险调整，捕捉波动率聚集环境下的动量延续/反转信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityRegimeMomentumSpread(BaseFactor):
    """用短长周期收益率差(5日-20日)刻画动量加速度，除以20日已实现波动率进行风险调整，捕捉波动率聚集环境下的动量延续/反转信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_regime_mom_diff",
            name="Volatility Regime Momentum Spread",
            display_name="波动率状态动量差",
            description="用短长周期收益率差(5日-20日)刻画动量加速度，除以20日已实现波动率进行风险调整，捕捉波动率聚集环境下的动量延续/反转信号。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        mom_short = data['close'].pct_change(5)
        mom_long = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        result = ((mom_short - mom_long) / (vol + 1e-9)).clip(-1, 1)
        return result
