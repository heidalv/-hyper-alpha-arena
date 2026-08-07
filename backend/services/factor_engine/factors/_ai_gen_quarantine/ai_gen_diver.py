"""AI因子: 价量背离因子 | 置信:60% | 计算价格变化与成交量变化之间的滚动相关性，当负相关显著时（即价格上涨但成交量下降或价格下跌但成交量上升），表明趋势不可持续，易引发多头亏损。输出负值表示背离风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Price_Volume_Divergence(BaseFactor):
    """计算价格变化与成交量变化之间的滚动相关性，当负相关显著时（即价格上涨但成交量下降或价格下跌但成交量上升），表明趋势不可持续，易引发多头亏损。输出负值表示背离风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_diver",
            name="Price-Volume Divergence",
            display_name="价量背离因子",
            description="计算价格变化与成交量变化之间的滚动相关性，当负相关显著时（即价格上涨但成交量下降或价格下跌但成交量上升），表明趋势不可持续，易引发多头亏损。输出负值表示背离风险。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        # 收益率
        ret = close.pct_change()
        vol_change = volume.pct_change()
        # 滚动相关系数（20期）
        corr = ret.rolling(20).corr(vol_change)
        # 当相关系数低于-0.3时，视为明显背离，输出负值（-1到0）
        result = -np.clip((corr + 0.3) / 0.7, 0, 1)
        return result.fillna(0.0)
