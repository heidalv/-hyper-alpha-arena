"""AI因子: 多空力量失衡 | 置信:50% | 通过比较收盘价在近期高低点的相对位置与成交量加权平均位置，衡量市场多空失衡程度。若价格接近区间顶部但成交量萎缩，则多头衰竭；接近底部放量则空头衰竭。值域[-1,1]指示空头/多头主导。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LongShortImbalance(BaseFactor):
    """通过比较收盘价在近期高低点的相对位置与成交量加权平均位置，衡量市场多空失衡程度。若价格接近区间顶部但成交量萎缩，则多头衰竭；接近底部放量则空头衰竭。值域[-1,1]指示空头/多头主导。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_longshort",
            name="LongShortImbalance",
            display_name="多空力量失衡",
            description="通过比较收盘价在近期高低点的相对位置与成交量加权平均位置，衡量市场多空失衡程度。若价格接近区间顶部但成交量萎缩，则多头衰竭；接近底部放量则空头衰竭。值域[-1,1]指示空头/多头主导。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        n = 14
        roll_high = high.rolling(n).max()
        roll_low = low.rolling(n).min()
        pos = (close - roll_low) / (roll_high - roll_low + 1e-10)
        vwap = (close * volume).rolling(n).sum() / (volume.rolling(n).sum() + 1e-10)
        vwap_pos = (vwap - roll_low) / (roll_high - roll_low + 1e-10)
        imbalance = pos - vwap_pos
        result = np.tanh(imbalance * 4)
        return result
