"""AI因子: 插针反转波动环境因子 | 置信:62% | 利用上下影线不对称度衡量买卖双方防守强度：下影主导(lower>upper)代表买方承接，未来反弹概率高；上影主导代表卖方拒绝，未来回落概率高。再用20日插针密度作为波动环境分位，在高波动环境下影线反转信号更有效，故将不对称度rolling均值与波动环境交互，输出[-1,1]的可测IC因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarReversalWithVolatilityRegime(BaseFactor):
    """利用上下影线不对称度衡量买卖双方防守强度：下影主导(lower>upper)代表买方承接，未来反弹概率高；上影主导代表卖方拒绝，未来回落概率高。再用20日插针密度作为波动环境分位，在高波动环境下影线反转信号更有效，故将不对称度rolling均值与波动环境交互，输出[-1,1]的可测IC因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_reversal_vol",
            name="Pin Bar Reversal with Volatility Regime",
            display_name="插针反转波动环境因子",
            description="利用上下影线不对称度衡量买卖双方防守强度：下影主导(lower>upper)代表买方承接，未来反弹概率高；上影主导代表卖方拒绝，未来回落概率高。再用20日插针密度作为波动环境分位，在高波动环境下影线反转信号更有效，故将不对称度rolling均值与波动环境交互，输出[-1,1]的可测IC因子。",
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
        pin_density = (data['high'] - data['low']).rolling(20).mean() / (body.rolling(20).mean() + 1e-9)
        regime = (pin_density / (pin_density.rolling(60).mean() + 1e-9)).clip(0, 3)
        result = (asym.rolling(3).mean() * regime).clip(-1, 1)
        return result
