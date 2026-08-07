"""AI因子: 量价确认因子 | 置信:72% | 基于收盘价变化与成交量变化在最近10期内的相关系数，负相关表示量价背离（成交量放大但价格反向，或缩量上涨），因子值负；正相关表示量价配合，因子值正。用于过滤伪趋势。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceConfirmationFactor(BaseFactor):
    """基于收盘价变化与成交量变化在最近10期内的相关系数，负相关表示量价背离（成交量放大但价格反向，或缩量上涨），因子值负；正相关表示量价配合，因子值正。用于过滤伪趋势。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volconf",
            name="Volume Price Confirmation Factor",
            display_name="量价确认因子",
            description="基于收盘价变化与成交量变化在最近10期内的相关系数，负相关表示量价背离（成交量放大但价格反向，或缩量上涨），因子值负；正相关表示量价配合，因子值正。用于过滤伪趋势。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算收益率和成交量变化率
        price_ret = data['close'].pct_change()
        vol_change = data['volume'].pct_change()
        # 滚动10期相关系数
        corr = price_ret.rolling(10).corr(vol_change).fillna(0)
        # 映射到[-1,1]
        factor = corr.clip(-1, 1)
        # 处理极端值：当相关系数绝对值小于0.2时视为无效信号，归0
        factor = np.where(np.abs(factor) < 0.2, 0, factor)
        return pd.Series(factor, index=data.index)
