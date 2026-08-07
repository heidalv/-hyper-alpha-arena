"""AI因子: 量价相关性 | 置信:60% | 计算过去20日价格涨跌幅与成交量变化率的滚动相关系数。正相关表示量价同步，趋势可靠；负相关表示量价背离，趋势脆弱。直接输出相关系数[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceCorrelation(BaseFactor):
    """计算过去20日价格涨跌幅与成交量变化率的滚动相关系数。正相关表示量价同步，趋势可靠；负相关表示量价背离，趋势脆弱。直接输出相关系数[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volprice_corr",
            name="Volume-Price Correlation",
            display_name="量价相关性",
            description="计算过去20日价格涨跌幅与成交量变化率的滚动相关系数。正相关表示量价同步，趋势可靠；负相关表示量价背离，趋势脆弱。直接输出相关系数[-1,1]。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        ret = data['close'].pct_change()
        vol_change = data['volume'].pct_change()
        # 滚动20日相关系数
        corr = ret.rolling(20).corr(vol_change)
        # 处理NaN，用0填充
        result = corr.fillna(0)
        return result
