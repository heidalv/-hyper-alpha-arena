"""AI因子: 趋势模糊指数 | 置信:60% | 基于简化版ADX（方向性运动指数）与价格相对于最近N周期高低的区间位置，当ADX低于阈值且价格处于区间中段时，趋势不明确，容易止损，输出负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendDisambiguityIndex(BaseFactor):
    """基于简化版ADX（方向性运动指数）与价格相对于最近N周期高低的区间位置，当ADX低于阈值且价格处于区间中段时，趋势不明确，容易止损，输出负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tdi",
            name="Trend Disambiguity Index",
            display_name="趋势模糊指数",
            description="基于简化版ADX（方向性运动指数）与价格相对于最近N周期高低的区间位置，当ADX低于阈值且价格处于区间中段时，趋势不明确，容易止损，输出负值。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        n = 14
        high = data['high']
        low = data['low']
        close = data['close']
        # 简化ADX（仅用+DM和-DM的均值）
        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.rolling(n).mean()
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
        plus_di = 100 * plus_dm.rolling(n).mean() / atr
        minus_di = 100 * minus_dm.rolling(n).mean() / atr
        dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-10) * 100
        adx = dx.rolling(n).mean()
        # 价格在N周期高低中的位置
        hh = high.rolling(n).max()
        ll = low.rolling(n).min()
        pos = (close - ll) / (hh - ll).replace(0, 1e-10)
        # 低ADX且价格在中间区域（0.3~0.7）表示趋势模糊
        cond = (adx < 20) & (pos.between(0.3, 0.7))
        result = -cond.astype(float) * 1.0
        result.fillna(0.0, inplace=True)
        return result
