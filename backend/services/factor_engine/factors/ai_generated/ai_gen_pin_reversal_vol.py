"""AI因子: 插针反转波动环境因子 | 置信:62% | 利用上下影线不对称度衡量买卖方防守强度，long lower shadow(下影主导)表示买方承接，预示反弹；用插针密度作为波动环境分位，在高波动环境下反转信号更可靠。因子值>0看涨，<0看跌。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarReversalWithVolatilityRegime(BaseFactor):
    """利用上下影线不对称度衡量买卖方防守强度，long lower shadow(下影主导)表示买方承接，预示反弹；用插针密度作为波动环境分位，在高波动环境下反转信号更可靠。因子值>0看涨，<0看跌。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_reversal_vol",
            name="Pin Bar Reversal with Volatility Regime",
            display_name="插针反转波动环境因子",
            description="利用上下影线不对称度衡量买卖方防守强度，long lower shadow(下影主导)表示买方承接，预示反弹；用插针密度作为波动环境分位，在高波动环境下反转信号更可靠。因子值>0看涨，<0看跌。",
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
        density = (np.maximum(upper, lower) / body).rolling(20).mean()
        result = (asym.rolling(3).mean() / (density + 1e-9)).clip(-1, 1)
        return result
