"""AI因子: 量价背离因子 | 置信:58% | 价格上行但成交量相对萎缩时，上涨动能不足，未来回落概率高；价格下行但缩量时抛压衰竭，未来反弹概率高。用成交量相对中位数比值与收益方向交互构造背离信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格上行但成交量相对萎缩时，上涨动能不足，未来回落概率高；价格下行但缩量时抛压衰竭，未来反弹概率高。用成交量相对中位数比值与收益方向交互构造背离信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_divergence_15",
            name="Volume-Price Divergence",
            display_name="量价背离因子",
            description="价格上行但成交量相对萎缩时，上涨动能不足，未来回落概率高；价格下行但缩量时抛压衰竭，未来反弹概率高。用成交量相对中位数比值与收益方向交互构造背离信号。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol_ratio = data['volume'] / (data['volume'].rolling(20).median() + 1e-9)
        result = (-ret * (1 - vol_ratio.clip(0, 2) / 2)).clip(-1, 1)
        return result
