"""AI因子: 插针影线不对称反转（波动门控） | 置信:62% | 计算上下影线不对称度(lower-upper)/body，衡量买方防守(下影主导)与卖方拒绝(上影主导)。用20日插针密度作为波动环境门控：高插针密度环境下影线信号更可靠。再与短期收益方向交互，捕捉插针后的均值回归alpha。因子值高表示下影承接+短期超跌，未来反弹概率高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversalWithVolatilityGate(BaseFactor):
    """计算上下影线不对称度(lower-upper)/body，衡量买方防守(下影主导)与卖方拒绝(上影主导)。用20日插针密度作为波动环境门控：高插针密度环境下影线信号更可靠。再与短期收益方向交互，捕捉插针后的均值回归alpha。因子值高表示下影承接+短期超跌，未来反弹概率高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pinbar_reversal_vol_gated",
            name="Pin Bar Asymmetry Reversal with Volatility Gate",
            display_name="插针影线不对称反转（波动门控）",
            description="计算上下影线不对称度(lower-upper)/body，衡量买方防守(下影主导)与卖方拒绝(上影主导)。用20日插针密度作为波动环境门控：高插针密度环境下影线信号更可靠。再与短期收益方向交互，捕捉插针后的均值回归alpha。因子值高表示下影承接+短期超跌，未来反弹概率高。",
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
        gate = density / (density.rolling(60).mean() + 1e-9)
        ret5 = data['close'].pct_change(5)
        result = (asym.rolling(3).mean() * gate * (-ret5)).clip(-1, 1)
        return result
