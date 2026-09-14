"""AI因子: 插针影线不对称反转 | 置信:62% | 计算下影线与上影线的不对称度（下影主导代表买方防守，上影主导代表卖方拒绝），并对其做短期滚动平滑，捕捉插针后的均值回归方向。因子值越高代表近期下影承接越强，未来上涨概率更高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """计算下影线与上影线的不对称度（下影主导代表买方防守，上影主导代表卖方拒绝），并对其做短期滚动平滑，捕捉插针后的均值回归方向。因子值越高代表近期下影承接越强，未来上涨概率更高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_bar_asym_reversal",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针影线不对称反转",
            description="计算下影线与上影线的不对称度（下影主导代表买方防守，上影主导代表卖方拒绝），并对其做短期滚动平滑，捕捉插针后的均值回归方向。因子值越高代表近期下影承接越强，未来上涨概率更高。",
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
        result = asym.rolling(3).mean().clip(-1, 1)
        return result
