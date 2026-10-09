"""AI因子: 量价背离因子 | 置信:58% | 比较短期价格变动方向与成交量变动方向的背离程度，价格上行但量能萎缩或价格下行但量能放大时给出反向信号，捕捉量价背离后的均值回归 alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """比较短期价格变动方向与成交量变动方向的背离程度，价格上行但量能萎缩或价格下行但量能放大时给出反向信号，捕捉量价背离后的均值回归 alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volprice_divergence",
            name="Volume Price Divergence",
            display_name="量价背离因子",
            description="比较短期价格变动方向与成交量变动方向的背离程度，价格上行但量能萎缩或价格下行但量能放大时给出反向信号，捕捉量价背离后的均值回归 alpha。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        pret = data['close'].pct_change(5)
        vret = data['volume'].pct_change(5)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((pret - vret) / vol).clip(-1, 1)
        return result
