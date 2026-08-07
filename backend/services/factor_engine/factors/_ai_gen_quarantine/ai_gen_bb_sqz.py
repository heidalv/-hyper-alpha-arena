"""AI因子: 布林带挤压 | 置信:65% | 衡量布林带宽度相对于近期历史的收缩程度，波动极度收缩时返回负值（易出现亏损震荡），扩张时返回正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class BollingerSqueeze(BaseFactor):
    """衡量布林带宽度相对于近期历史的收缩程度，波动极度收缩时返回负值（易出现亏损震荡），扩张时返回正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bb_sqz",
            name="Bollinger Squeeze",
            display_name="布林带挤压",
            description="衡量布林带宽度相对于近期历史的收缩程度，波动极度收缩时返回负值（易出现亏损震荡），扩张时返回正值。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        basis = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper = basis + 2 * std20
        lower = basis - 2 * std20
        bbw = (upper - lower) / basis
        bbw_mean = bbw.rolling(100).mean()
        bbw_std = bbw.rolling(100).std()
        zscore = (bbw - bbw_mean) / (bbw_std + 1e-9)
        result = (zscore / 2).clip(-1, 1)
        return result.rename('ai_gen_bb_sqz')
