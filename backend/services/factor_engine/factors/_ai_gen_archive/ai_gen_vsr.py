"""AI因子: 放量反转信号 | 置信:50% | 针对SL止损模式中放量下跌的特征。当成交量突然放大且价格下跌时，因子值趋近-1，提示可能继续下跌；若放量但价格上涨则趋近+1。结合短期动量过滤噪音。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeSurgeReversal(BaseFactor):
    """针对SL止损模式中放量下跌的特征。当成交量突然放大且价格下跌时，因子值趋近-1，提示可能继续下跌；若放量但价格上涨则趋近+1。结合短期动量过滤噪音。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vsr",
            name="volume_surge_reversal",
            display_name="放量反转信号",
            description="针对SL止损模式中放量下跌的特征。当成交量突然放大且价格下跌时，因子值趋近-1，提示可能继续下跌；若放量但价格上涨则趋近+1。结合短期动量过滤噪音。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        vol_ratio = data['volume'] / (data['volume'].rolling(20).mean() + 1e-9)
        price_chg = data['close'].pct_change(3)
        result = (vol_ratio * price_chg).clip(-1, 1)
        return result
