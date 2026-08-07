"""AI因子: 区间震荡检测 | 置信:60% | 通过布林带宽度衡量市场波动区间。布林带宽度极窄时市场陷入盘整，突破困难，容易造成持仓超时亏损，因子趋向-1；宽度扩张时趋势环境良好，趋向+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RangeBoundDetector(BaseFactor):
    """通过布林带宽度衡量市场波动区间。布林带宽度极窄时市场陷入盘整，突破困难，容易造成持仓超时亏损，因子趋向-1；宽度扩张时趋势环境良好，趋向+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rbd",
            name="Range-Bound Detector",
            display_name="区间震荡检测",
            description="通过布林带宽度衡量市场波动区间。布林带宽度极窄时市场陷入盘整，突破困难，容易造成持仓超时亏损，因子趋向-1；宽度扩张时趋势环境良好，趋向+1。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        sma = close.rolling(20).mean()
        std = close.rolling(20).std()
        bb_width = 2 * std / sma
        rank = bb_width.rolling(100, min_periods=10).rank(pct=True)
        result = rank * 2 - 1
        result = result.fillna(0).clip(-1, 1)
        return result
