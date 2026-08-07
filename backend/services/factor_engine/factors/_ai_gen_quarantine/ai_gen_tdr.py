"""AI因子: 时间衰减风险 | 置信:60% | 通过实体与振幅比率识别市场犹豫状态。当K线实体占比持续偏低时，多空僵持不下，持仓时间消耗快，易发生超时亏损。因子值[-1,1]，负值表示犹豫风险高，正值表示方向明确。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TimeDecayRisk(BaseFactor):
    """通过实体与振幅比率识别市场犹豫状态。当K线实体占比持续偏低时，多空僵持不下，持仓时间消耗快，易发生超时亏损。因子值[-1,1]，负值表示犹豫风险高，正值表示方向明确。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tdr",
            name="Time Decay Risk",
            display_name="时间衰减风险",
            description="通过实体与振幅比率识别市场犹豫状态。当K线实体占比持续偏低时，多空僵持不下，持仓时间消耗快，易发生超时亏损。因子值[-1,1]，负值表示犹豫风险高，正值表示方向明确。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        open_ = data['open']
        high = data['high']
        low = data['low']
        close = data['close']
        period = 10
        lookback = 60
        body = (close - open_).abs()
        total_range = high - low
        # avoid division by zero
        body_ratio = body / total_range.replace(0, 1e-9)
        # smooth ratio
        smooth_ratio = body_ratio.rolling(period).mean()
        # normalize to [-1, 1] via historical percentile
        rank = smooth_ratio.rolling(lookback).rank(pct=True)
        result = (rank - 0.5) * 2.0
        result = result.fillna(0).clip(-1, 1)
        return result
