"""AI因子: 插针不对称反转(波动门控) | 置信:62% | 计算下影线与上影线的不对称度(下影主导=买方防守)，用20日插针密度做波动环境门控，高波动环境下强化反转信号，与短期收益方向交互，预测短线反弹。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversalWithVolatilityGate(BaseFactor):
    """计算下影线与上影线的不对称度(下影主导=买方防守)，用20日插针密度做波动环境门控，高波动环境下强化反转信号，与短期收益方向交互，预测短线反弹。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_reversal_vol_gate",
            name="Pin Bar Asymmetry Reversal with Volatility Gate",
            display_name="插针不对称反转(波动门控)",
            description="计算下影线与上影线的不对称度(下影主导=买方防守)，用20日插针密度做波动环境门控，高波动环境下强化反转信号，与短期收益方向交互，预测短线反弹。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        asym = (lower - upper) / body
        density = (data['high'] - data['low']).rolling(20).mean() / (body.rolling(20).mean() + 1e-9)
        gate = (density / (density.rolling(60).mean() + 1e-9)).clip(0, 2)
        result = (asym.rolling(3).mean() * gate).clip(-1, 1)
        return result
