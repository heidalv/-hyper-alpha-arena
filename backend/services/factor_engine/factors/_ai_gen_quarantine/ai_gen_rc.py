"""AI因子: 区间收缩 | 置信:70% | 布林带宽度百分位排名，值接近-1表示低波动盘整，+1表示高波动趋势扩张。盘整区间容易导致趋势策略持仓超时或反复止损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RangeContraction(BaseFactor):
    """布林带宽度百分位排名，值接近-1表示低波动盘整，+1表示高波动趋势扩张。盘整区间容易导致趋势策略持仓超时或反复止损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rc",
            name="Range Contraction",
            display_name="区间收缩",
            description="布林带宽度百分位排名，值接近-1表示低波动盘整，+1表示高波动趋势扩张。盘整区间容易导致趋势策略持仓超时或反复止损。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ma = close.rolling(20).mean()
        std = close.rolling(20).std()
        bb_width = (2 * std) / ma
        rank = bb_width.rolling(100).rank(pct=True)
        result = 2 * rank - 1
        return result.fillna(0)
