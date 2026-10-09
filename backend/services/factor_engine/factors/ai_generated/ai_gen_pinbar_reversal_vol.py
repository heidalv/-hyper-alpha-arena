"""AI因子: 插针反转波动环境因子 | 置信:62% | 结合上下影线不对称度与波动环境。长下影线(买方承接)后短期反弹概率高，长上影线(卖方拒绝)后回落概率高。用影线不对称度的rolling均值与短期收益方向交互，并除以波动率环境归一化，捕捉插针后的均值回归alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinbarReversalWithVolatilityRegime(BaseFactor):
    """结合上下影线不对称度与波动环境。长下影线(买方承接)后短期反弹概率高，长上影线(卖方拒绝)后回落概率高。用影线不对称度的rolling均值与短期收益方向交互，并除以波动率环境归一化，捕捉插针后的均值回归alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pinbar_reversal_vol",
            name="Pinbar Reversal with Volatility Regime",
            display_name="插针反转波动环境因子",
            description="结合上下影线不对称度与波动环境。长下影线(买方承接)后短期反弹概率高，长上影线(卖方拒绝)后回落概率高。用影线不对称度的rolling均值与短期收益方向交互，并除以波动率环境归一化，捕捉插针后的均值回归alpha。",
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
        pin_density = (data['high'] - data['low']) / body
        vol = data['close'].pct_change().rolling(20).std()
        short_ret = data['close'].pct_change(3)
        result = (asym.rolling(3).mean() * (-short_ret) / (vol + 1e-9)).clip(-1, 1)
        return result
