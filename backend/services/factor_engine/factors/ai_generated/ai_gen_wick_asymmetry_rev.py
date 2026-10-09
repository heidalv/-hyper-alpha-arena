"""AI因子: 影线不对称反转 | 置信:62% | 基于K线上下影线不对称度构造插针反转因子。下影线主导（lower>upper）表示买方在低位承接，未来反弹概率高；上影线主导表示卖方在高位拒绝，未来回落概率高。对不对称度做3期滚动平滑后截断至[-1,1]，作为方向性alpha信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class WickAsymmetryReversal(BaseFactor):
    """基于K线上下影线不对称度构造插针反转因子。下影线主导（lower>upper）表示买方在低位承接，未来反弹概率高；上影线主导表示卖方在高位拒绝，未来回落概率高。对不对称度做3期滚动平滑后截断至[-1,1]，作为方向性alpha信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_wick_asymmetry_rev",
            name="Wick Asymmetry Reversal",
            display_name="影线不对称反转",
            description="基于K线上下影线不对称度构造插针反转因子。下影线主导（lower>upper）表示买方在低位承接，未来反弹概率高；上影线主导表示卖方在高位拒绝，未来回落概率高。对不对称度做3期滚动平滑后截断至[-1,1]，作为方向性alpha信号。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        result = ((lower - upper) / body).rolling(3).mean().clip(-1, 1)
        return result
