"""AI因子: 插针影线不对称反转 | 置信:60% | 计算上下影线不对称度(lower-upper)/body，取3期滚动均值后与短期收益方向交互：下影主导(买方防守)且近期下跌时给正分，预期反弹；上影主导(卖方拒绝)且近期上涨时给负分，预期回落。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarAsymmetryReversal5(BaseFactor):
    """计算上下影线不对称度(lower-upper)/body，取3期滚动均值后与短期收益方向交互：下影主导(买方防守)且近期下跌时给正分，预期反弹；上影主导(卖方拒绝)且近期上涨时给负分，预期回落。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_bar_reversal_5",
            name="Pin Bar Asymmetry Reversal 5",
            display_name="插针影线不对称反转",
            description="计算上下影线不对称度(lower-upper)/body，取3期滚动均值后与短期收益方向交互：下影主导(买方防守)且近期下跌时给正分，预期反弹；上影主导(卖方拒绝)且近期上涨时给负分，预期回落。",
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
        ret = data['close'].pct_change(3)
        result = (asym * (-ret)).rolling(5).mean().clip(-1, 1)
        return result
