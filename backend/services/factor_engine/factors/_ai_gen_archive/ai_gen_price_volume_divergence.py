"""AI因子: 量价背离因子 | 置信:55% | 识别价格创新高但成交量未同步放大的顶背离，或价格创新低但成交量萎缩的底背离。背离往往预示趋势动能衰竭，随后发生反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PriceVolumeDivergence(BaseFactor):
    """识别价格创新高但成交量未同步放大的顶背离，或价格创新低但成交量萎缩的底背离。背离往往预示趋势动能衰竭，随后发生反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_price_volume_divergence",
            name="Price_Volume_Divergence",
            display_name="量价背离因子",
            description="识别价格创新高但成交量未同步放大的顶背离，或价格创新低但成交量萎缩的底背离。背离往往预示趋势动能衰竭，随后发生反转。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        price_ret = data['close'].pct_change(5)
        vol_ret = data['volume'].pct_change(5)
        result = (price_ret - vol_ret).clip(-1, 1)
        return result
