"""AI因子: 价格效率比率 | 置信:65% | 衡量价格路径的直线程度，高值表示趋势连贯（高效），低值表示震荡/无序（regime=unknown）。基于Kaufman效率比，映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PriceEfficiencyRatio(BaseFactor):
    """衡量价格路径的直线程度，高值表示趋势连贯（高效），低值表示震荡/无序（regime=unknown）。基于Kaufman效率比，映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_eff_ratio",
            name="PriceEfficiencyRatio",
            display_name="价格效率比率",
            description="衡量价格路径的直线程度，高值表示趋势连贯（高效），低值表示震荡/无序（regime=unknown）。基于Kaufman效率比，映射到[-1,1]。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        n = 14
        close = data['close']
        direction = close.diff(n).abs()
        volatility = close.diff().abs().rolling(window=n).sum()
        er = direction / (volatility + 1e-10)
        # er范围0-1，映射到[-1,1]，0表示震荡-1，1表示趋势+1
        result = 2 * er - 1
        result = result.clip(-1, 1)
        return result.fillna(0)
