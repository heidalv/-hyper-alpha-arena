"""AI因子: 成交量枯竭指标 | 置信:60% | 检测成交量相对其历史均值的萎缩程度。成交量低迷时市场缺乏方向性推力，持仓易陷入漂移并导致超时亏损，因子趋向-1；放量时趋向+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeDryUpIndicator(BaseFactor):
    """检测成交量相对其历史均值的萎缩程度。成交量低迷时市场缺乏方向性推力，持仓易陷入漂移并导致超时亏损，因子趋向-1；放量时趋向+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vdu",
            name="Volume Dry-Up Indicator",
            display_name="成交量枯竭指标",
            description="检测成交量相对其历史均值的萎缩程度。成交量低迷时市场缺乏方向性推力，持仓易陷入漂移并导致超时亏损，因子趋向-1；放量时趋向+1。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        volume = data['volume']
        vol_sma = volume.rolling(20).mean()
        ratio = volume / vol_sma
        rank = ratio.rolling(100, min_periods=10).rank(pct=True)
        result = rank * 2 - 1
        result = result.fillna(0).clip(-1, 1)
        return result
