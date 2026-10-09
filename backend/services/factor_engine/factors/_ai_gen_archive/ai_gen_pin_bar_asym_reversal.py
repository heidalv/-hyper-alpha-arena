"""AI因子: 插针影线不对称反转 | 置信:62% | 基于生产插针判定同源公式，计算(下影线-上影线)/实体的不对称度，并在3根K线上平滑。下影主导（买方防守）给正信号，上影主导（卖方拒绝）给负信号，捕捉短线插针后的均值回归方向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """基于生产插针判定同源公式，计算(下影线-上影线)/实体的不对称度，并在3根K线上平滑。下影主导（买方防守）给正信号，上影主导（卖方拒绝）给负信号，捕捉短线插针后的均值回归方向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_bar_asym_reversal",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针影线不对称反转",
            description="基于生产插针判定同源公式，计算(下影线-上影线)/实体的不对称度，并在3根K线上平滑。下影主导（买方防守）给正信号，上影主导（卖方拒绝）给负信号，捕捉短线插针后的均值回归方向。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        result = ((lower - upper) / body).rolling(3).mean().clip(-1, 1)
        return result
