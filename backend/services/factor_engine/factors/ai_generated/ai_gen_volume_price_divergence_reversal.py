"""AI因子: 量价背离反转 | 置信:58% | 当价格短期上涨但成交量相对萎缩时，上涨缺乏资金确认，未来回落概率更高；当价格下跌但成交量放大时，恐慌抛售可能接近尾声，未来反弹概率更高。以价格变动方向与量能相对变化的乘积构造背离信号，输出[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergenceReversal(BaseFactor):
    """当价格短期上涨但成交量相对萎缩时，上涨缺乏资金确认，未来回落概率更高；当价格下跌但成交量放大时，恐慌抛售可能接近尾声，未来反弹概率更高。以价格变动方向与量能相对变化的乘积构造背离信号，输出[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_price_divergence_reversal",
            name="Volume Price Divergence Reversal",
            display_name="量价背离反转",
            description="当价格短期上涨但成交量相对萎缩时，上涨缺乏资金确认，未来回落概率更高；当价格下跌但成交量放大时，恐慌抛售可能接近尾声，未来反弹概率更高。以价格变动方向与量能相对变化的乘积构造背离信号，输出[-1,1]。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol_chg = data['volume'].rolling(5).mean() / (data['volume'].rolling(20).mean() + 1e-9) - 1
        result = (-ret * vol_chg).clip(-1, 1)
        return result
