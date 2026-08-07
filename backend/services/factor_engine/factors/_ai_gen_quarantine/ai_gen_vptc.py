"""AI因子: 量价趋势确认 | 置信:60% | 计算过去20日内收盘价方向与成交量方向一致的交易日占比，映射至[-1,1]。正值表示量价同向(趋势可靠，适合做多)，负值表示背离(趋势不可靠，应避免做多)。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Price_Trend_Confirmation(BaseFactor):
    """计算过去20日内收盘价方向与成交量方向一致的交易日占比，映射至[-1,1]。正值表示量价同向(趋势可靠，适合做多)，负值表示背离(趋势不可靠，应避免做多)。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vptc",
            name="Volume-Price Trend Confirmation",
            display_name="量价趋势确认",
            description="计算过去20日内收盘价方向与成交量方向一致的交易日占比，映射至[-1,1]。正值表示量价同向(趋势可靠，适合做多)，负值表示背离(趋势不可靠，应避免做多)。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        price_dir = close.diff().fillna(0).apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        vol_dir = volume.diff().fillna(0).apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        agree = (price_dir == vol_dir).astype(int)
        ratio = agree.rolling(20).mean()
        result = 2.0 * ratio - 1.0
        result = result.fillna(0)
        return result
