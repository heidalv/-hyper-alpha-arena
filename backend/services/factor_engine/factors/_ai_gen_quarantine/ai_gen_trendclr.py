"""AI因子: 趋势清晰度 | 置信:60% | 基于价格效率比（Efficiency Ratio）衡量趋势明确性，ER接近1为强趋势，接近0为震荡。输出映射到[-1,1]，正数表示趋势清晰适合交易，负数表示震荡市应避免。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendClarity(BaseFactor):
    """基于价格效率比（Efficiency Ratio）衡量趋势明确性，ER接近1为强趋势，接近0为震荡。输出映射到[-1,1]，正数表示趋势清晰适合交易，负数表示震荡市应避免。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trendclr",
            name="Trend Clarity",
            display_name="趋势清晰度",
            description="基于价格效率比（Efficiency Ratio）衡量趋势明确性，ER接近1为强趋势，接近0为震荡。输出映射到[-1,1]，正数表示趋势清晰适合交易，负数表示震荡市应避免。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        n = 20
        close = data['close']
        direction = close.diff(n).abs()
        volatility = close.diff().abs().rolling(n).sum()
        er = direction / volatility.replace(0, np.nan)
        result = 2 * er - 1
        return result.fillna(0).clip(-1, 1)
