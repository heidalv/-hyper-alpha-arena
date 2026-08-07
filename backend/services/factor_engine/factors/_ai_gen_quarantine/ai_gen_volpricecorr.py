"""AI因子: 量价确认度 | 置信:55% | 计算过去20期价格变化与成交量变化的滚动相关系数，若相关系数绝对值低（<0.3）则量价不匹配，市场状态不明易亏损，输出-1；否则输出+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceConfirmationIndicator(BaseFactor):
    """计算过去20期价格变化与成交量变化的滚动相关系数，若相关系数绝对值低（<0.3）则量价不匹配，市场状态不明易亏损，输出-1；否则输出+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volpricecorr",
            name="Volume-Price Confirmation Indicator",
            display_name="量价确认度",
            description="计算过去20期价格变化与成交量变化的滚动相关系数，若相关系数绝对值低（<0.3）则量价不匹配，市场状态不明易亏损，输出-1；否则输出+1。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        ret = close.pct_change()
        vol_change = volume.pct_change()
        corr = ret.rolling(20).corr(vol_change)
        result = np.where(corr.abs() < 0.3, -1.0, 1.0)
        return pd.Series(result, index=data.index)
