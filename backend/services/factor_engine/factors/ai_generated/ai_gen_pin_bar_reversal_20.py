"""AI因子: 插针影线不对称反转 | 置信:62% | 计算下影线与上影线相对实体的不对称度，衡量买方防守（长下影）或卖方拒绝（长上影）强度。取短期滚动均值与近5日收益方向交互：当出现长下影且价格短期下跌时，均值回归反弹概率上升，因子值取正；长上影且短期上涨时取负。捕捉插针后的短线反转 alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal(BaseFactor):
    """计算下影线与上影线相对实体的不对称度，衡量买方防守（长下影）或卖方拒绝（长上影）强度。取短期滚动均值与近5日收益方向交互：当出现长下影且价格短期下跌时，均值回归反弹概率上升，因子值取正；长上影且短期上涨时取负。捕捉插针后的短线反转 alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_bar_reversal_20",
            name="Pin Bar Asymmetry Reversal",
            display_name="插针影线不对称反转",
            description="计算下影线与上影线相对实体的不对称度，衡量买方防守（长下影）或卖方拒绝（长上影）强度。取短期滚动均值与近5日收益方向交互：当出现长下影且价格短期下跌时，均值回归反弹概率上升，因子值取正；长上影且短期上涨时取负。捕捉插针后的短线反转 alpha。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        asym = ((lower - upper) / body).rolling(3).mean()
        ret5 = data['close'].pct_change(5)
        result = (asym * (-ret5)).rolling(5).mean().clip(-1, 1)
        return result
