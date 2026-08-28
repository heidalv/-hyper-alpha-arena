"""AI因子: 未知状态动量反转 | 置信:50% | 所有亏损均在regime=unknown，且多空双向均亏，表明在状态不明时动量失效。该因子检测价格远离均线但成交量萎缩的假突破，做反向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Unknownregimemomentum(BaseFactor):
    """所有亏损均在regime=unknown，且多空双向均亏，表明在状态不明时动量失效。该因子检测价格远离均线但成交量萎缩的假突破，做反向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unknown_regime",
            name="UnknownRegimeMomentum",
            display_name="未知状态动量反转",
            description="所有亏损均在regime=unknown，且多空双向均亏，表明在状态不明时动量失效。该因子检测价格远离均线但成交量萎缩的假突破，做反向。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ma = data['close'].rolling(30).mean()
        dist = (data['close'] - ma) / (ma + 1e-9)
        vol_ratio = data['volume'] / data['volume'].rolling(30).mean()
        result = (dist * (1 - vol_ratio)).clip(-1, 1)
        return result
