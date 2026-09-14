"""AI因子: 插针反转波动率环境因子 | 置信:62% | 利用上下影线不对称度衡量买卖方防守强度，长下影线(买方承接)预示反弹，长上影线(卖方拒绝)预示回落；再以插针密度作为波动环境分位进行加权，在高插针密度(震荡/插针密集)环境下反转信号更可靠。因子值>0看多，<0看空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarReversalWithVolatilityRegime(BaseFactor):
    """利用上下影线不对称度衡量买卖方防守强度，长下影线(买方承接)预示反弹，长上影线(卖方拒绝)预示回落；再以插针密度作为波动环境分位进行加权，在高插针密度(震荡/插针密集)环境下反转信号更可靠。因子值>0看多，<0看空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_reversal_vol",
            name="Pin Bar Reversal with Volatility Regime",
            display_name="插针反转波动率环境因子",
            description="利用上下影线不对称度衡量买卖方防守强度，长下影线(买方承接)预示反弹，长上影线(卖方拒绝)预示回落；再以插针密度作为波动环境分位进行加权，在高插针密度(震荡/插针密集)环境下反转信号更可靠。因子值>0看多，<0看空。",
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
        pin_density = (data[['high','low']].max(axis=1) - data[['high','low']].min(axis=1)) / body
        env = pin_density.rolling(20).mean()
        env_norm = (env / (env.rolling(60).mean() + 1e-9)).clip(0, 3)
        result = (asym.rolling(3).mean() * env_norm).clip(-1, 1)
        return result
