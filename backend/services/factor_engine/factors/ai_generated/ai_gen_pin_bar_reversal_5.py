"""AI因子: 插针影线不对称反转5 | 置信:62% | 计算下影线与上影线的不对称度（下影主导为买方防守），取3期滚动均值后与5期短期收益方向交互。长下影承接后反弹、长上影拒绝后回落，捕捉短线插针反转alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal5(BaseFactor):
    """计算下影线与上影线的不对称度（下影主导为买方防守），取3期滚动均值后与5期短期收益方向交互。长下影承接后反弹、长上影拒绝后回落，捕捉短线插针反转alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_bar_reversal_5",
            name="Pin Bar Asymmetry Reversal 5",
            display_name="插针影线不对称反转5",
            description="计算下影线与上影线的不对称度（下影主导为买方防守），取3期滚动均值后与5期短期收益方向交互。长下影承接后反弹、长上影拒绝后回落，捕捉短线插针反转alpha。",
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
        ret5 = data['close'].pct_change(5)
        result = (asym.rolling(3).mean() * (-ret5).clip(-0.1, 0.1) * 10).clip(-1, 1)
        return result
