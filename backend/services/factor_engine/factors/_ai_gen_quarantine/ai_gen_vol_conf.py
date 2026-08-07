"""AI因子: 成交量-价格确认因子 | 置信:45% | 通过滚动窗口内价格变化与成交量变化的相关系数衡量量价关系，负相关（价格上涨但缩量或下跌但放量）输出负值，正相关输出正值，避免在量价背离的未知状态下做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Price_Confirmation(BaseFactor):
    """通过滚动窗口内价格变化与成交量变化的相关系数衡量量价关系，负相关（价格上涨但缩量或下跌但放量）输出负值，正相关输出正值，避免在量价背离的未知状态下做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_conf",
            name="Volume-Price Confirmation",
            display_name="成交量-价格确认因子",
            description="通过滚动窗口内价格变化与成交量变化的相关系数衡量量价关系，负相关（价格上涨但缩量或下跌但放量）输出负值，正相关输出正值，避免在量价背离的未知状态下做多。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        df = data.copy()
        window = 10
        price_ret = df['close'].pct_change()
        vol_ret = df['volume'].pct_change()
        corr = price_ret.rolling(window).corr(vol_ret)
        result = corr.fillna(0).clip(-1, 1)
        return result
