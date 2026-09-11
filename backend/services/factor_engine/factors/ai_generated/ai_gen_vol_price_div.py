"""AI因子: 量价背离因子 | 置信:55% | 当价格上涨但成交量萎缩时，说明上涨动能不足，未来可能回落；当价格下跌但成交量萎缩时，说明抛压减轻，可能反弹。用价格变化与成交量变化的符号背离构造反转信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """当价格上涨但成交量萎缩时，说明上涨动能不足，未来可能回落；当价格下跌但成交量萎缩时，说明抛压减轻，可能反弹。用价格变化与成交量变化的符号背离构造反转信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_div",
            name="Volume Price Divergence",
            display_name="量价背离因子",
            description="当价格上涨但成交量萎缩时，说明上涨动能不足，未来可能回落；当价格下跌但成交量萎缩时，说明抛压减轻，可能反弹。用价格变化与成交量变化的符号背离构造反转信号。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        price_chg = data['close'].pct_change(5)
        vol_chg = data['volume'].pct_change(5)
        vol_ma = data['volume'].rolling(20).mean() + 1e-9
        norm_vol = data['volume'] / vol_ma
        result = (-price_chg * (1 - norm_vol.clip(0, 2))).rolling(3).mean().clip(-1, 1)
        return result
