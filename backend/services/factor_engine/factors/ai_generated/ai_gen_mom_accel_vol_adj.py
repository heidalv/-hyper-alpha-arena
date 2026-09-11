"""AI因子: 波动调整动量加速度 | 置信:60% | 短期动量(5日收益)减去中期动量(20日收益)得到动量加速度，再除以20日已实现波动率做风险归一化。正值表示动量正在加速上行，预期未来收益方向偏多，属于经典动量类alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAccelerationVolAdjusted(BaseFactor):
    """短期动量(5日收益)减去中期动量(20日收益)得到动量加速度，再除以20日已实现波动率做风险归一化。正值表示动量正在加速上行，预期未来收益方向偏多，属于经典动量类alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_vol_adj",
            name="Momentum Acceleration Vol Adjusted",
            display_name="波动调整动量加速度",
            description="短期动量(5日收益)减去中期动量(20日收益)得到动量加速度，再除以20日已实现波动率做风险归一化。正值表示动量正在加速上行，预期未来收益方向偏多，属于经典动量类alpha。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        mom_s = data['close'].pct_change(5)
        mom_l = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        result = ((mom_s - mom_l) / (vol + 1e-9)).clip(-1, 1)
        return result
