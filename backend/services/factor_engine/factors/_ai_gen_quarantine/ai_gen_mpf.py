"""AI因子: 多空力量流 | 置信:60% | 通过比较当前收盘价在近期价格区间内的位置与成交量变化，判断买方动能的持续性。当价格处于区间高位但成交量萎缩时，预示多头衰竭，因子值偏向负值；反之，价格低位放量则偏向正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MarketPowerFlow(BaseFactor):
    """通过比较当前收盘价在近期价格区间内的位置与成交量变化，判断买方动能的持续性。当价格处于区间高位但成交量萎缩时，预示多头衰竭，因子值偏向负值；反之，价格低位放量则偏向正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mpf",
            name="MarketPowerFlow",
            display_name="多空力量流",
            description="通过比较当前收盘价在近期价格区间内的位置与成交量变化，判断买方动能的持续性。当价格处于区间高位但成交量萎缩时，预示多头衰竭，因子值偏向负值；反之，价格低位放量则偏向正值。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']

        # 近期区间（20日）
        period = 20
        rolling_high = high.rolling(period).max()
        rolling_low = low.rolling(period).min()
        # 价格相对位置 (0~1)
        pos = (close - rolling_low) / (rolling_high - rolling_low + 1e-10)
        # 成交量相对变化（当前 vs 过去20日均值）
        vol_ma = volume.rolling(period).mean()
        vol_ratio = volume / (vol_ma + 1e-10)
        # 多空指标：高位置+缩量 => 负，低位置+放量 => 正
        # 使用 sigmoid 映射到 -1~1
        score = (0.5 - pos) * (vol_ratio - 1.0) * 2.0
        # 平滑并截断
        result = score.clip(-1, 1)
        return result
