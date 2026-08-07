"""AI因子: 趋势效率比 | 置信:60% | 基于价格净变化与总路径长度之比，衡量趋势效率。正值表示上升趋势高效，负值表示下跌趋势高效，接近零为震荡市。震荡市中开仓易导致超时退出，此因子可辅助识别市场状态。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendEfficiencyRatio(BaseFactor):
    """基于价格净变化与总路径长度之比，衡量趋势效率。正值表示上升趋势高效，负值表示下跌趋势高效，接近零为震荡市。震荡市中开仓易导致超时退出，此因子可辅助识别市场状态。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_eff",
            name="Trend Efficiency Ratio",
            display_name="趋势效率比",
            description="基于价格净变化与总路径长度之比，衡量趋势效率。正值表示上升趋势高效，负值表示下跌趋势高效，接近零为震荡市。震荡市中开仓易导致超时退出，此因子可辅助识别市场状态。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        period = 10
        change = close.diff(period)
        path = close.diff().abs().rolling(period).sum()
        er = change / path
        er = er.fillna(0)
        result = er.rolling(3).mean()
        return result.clip(-1, 1)
