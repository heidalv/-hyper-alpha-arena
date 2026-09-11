"""AI因子: 插针不对称反转(波动环境加权) | 置信:62% | 利用上下影线不对称度衡量买卖方防守强度：下影主导(lower>upper)表示买方承接，未来反弹概率高；上影主导表示卖方拒绝，未来回落概率高。将不对称度的短期rolling均值与波动环境(插针密度)交互，在插针密集环境下反转信号更强，输出值域[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversalWithVolatilityContext(BaseFactor):
    """利用上下影线不对称度衡量买卖方防守强度：下影主导(lower>upper)表示买方承接，未来反弹概率高；上影主导表示卖方拒绝，未来回落概率高。将不对称度的短期rolling均值与波动环境(插针密度)交互，在插针密集环境下反转信号更强，输出值域[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_reversal_vol",
            name="Pin Bar Asymmetry Reversal with Volatility Context",
            display_name="插针不对称反转(波动环境加权)",
            description="利用上下影线不对称度衡量买卖方防守强度：下影主导(lower>upper)表示买方承接，未来反弹概率高；上影主导表示卖方拒绝，未来回落概率高。将不对称度的短期rolling均值与波动环境(插针密度)交互，在插针密集环境下反转信号更强，输出值域[-1,1]。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        asym = (lower - upper) / body
        density = (data[['high','low']].max(axis=1) - data[['high','low']].min(axis=1)) / body
        env = density.rolling(20).mean()
        result = (asym.rolling(3).mean() * (env / (env.rolling(20).mean() + 1e-9))).clip(-1, 1)
        return result
