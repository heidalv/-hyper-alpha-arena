"""AI因子: 趋势弱势 | 置信:50% | 计算价格在20日内的位置，结合短期波动率。当价格处于区间中部且波动率上升时，表明趋势不明朗，容易发生逆势亏损。该因子在趋势不明时给出中性偏空信号，避免在unknown regime下盲目做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendWeakness(BaseFactor):
    """计算价格在20日内的位置，结合短期波动率。当价格处于区间中部且波动率上升时，表明趋势不明朗，容易发生逆势亏损。该因子在趋势不明时给出中性偏空信号，避免在unknown regime下盲目做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_weakness",
            name="Trend Weakness",
            display_name="趋势弱势",
            description="计算价格在20日内的位置，结合短期波动率。当价格处于区间中部且波动率上升时，表明趋势不明朗，容易发生逆势亏损。该因子在趋势不明时给出中性偏空信号，避免在unknown regime下盲目做多。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high20 = data['high'].rolling(20).max()
        low20 = data['low'].rolling(20).min()
        pos = (data['close'] - low20) / (high20 - low20 + 1e-9)
        vol = data['close'].pct_change().rolling(10).std()
        result = (0.5 - pos) * (vol / (vol + 1e-9))
        return result.clip(-1, 1)
