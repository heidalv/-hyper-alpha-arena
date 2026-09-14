"""AI因子: 量价背离因子 | 置信:55% | 价格短期涨幅与成交量变化方向不一致时提示反转。计算5日收益与5日成交量变化率的符号差异，量增价跌或量缩价涨均产生负向信号，量价同向则信号弱。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格短期涨幅与成交量变化方向不一致时提示反转。计算5日收益与5日成交量变化率的符号差异，量增价跌或量缩价涨均产生负向信号，量价同向则信号弱。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_divergence",
            name="Volume Price Divergence",
            display_name="量价背离因子",
            description="价格短期涨幅与成交量变化方向不一致时提示反转。计算5日收益与5日成交量变化率的符号差异，量增价跌或量缩价涨均产生负向信号，量价同向则信号弱。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol_chg = data['volume'].pct_change(5)
        sign = (ret * vol_chg)
        result = (-sign / (sign.abs() + 1e-9) * (ret.abs() / (ret.abs() + vol_chg.abs() + 1e-9))).clip(-1, 1)
        return result
