"""AI因子: 趋势均值突破反转 | 置信:55% | 亏损多为max_hold_timeout，表明趋势延续失败，采用20日动量与价格相对均线位置结合，当动量强但偏离均线过远时反向，避免追高杀跌"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trendmeanbreakreversal(BaseFactor):
    """亏损多为max_hold_timeout，表明趋势延续失败，采用20日动量与价格相对均线位置结合，当动量强但偏离均线过远时反向，避免追高杀跌"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tmbrk",
            name="TrendMeanBreakReversal",
            display_name="趋势均值突破反转",
            description="亏损多为max_hold_timeout，表明趋势延续失败，采用20日动量与价格相对均线位置结合，当动量强但偏离均线过远时反向，避免追高杀跌",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ma = close.rolling(20).mean()
        mom = close.pct_change(20)
        dev = (close - ma) / (ma + 1e-9)
        result = (mom * 0.7 - dev * 1.3).clip(-1, 1)
        return result
