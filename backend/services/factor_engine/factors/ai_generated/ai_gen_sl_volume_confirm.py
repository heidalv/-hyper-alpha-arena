"""AI因子: 止损量能确认 | 置信:50% | 针对SL止损亏损（占比约40%），这些亏损往往伴随放量突破失败。该因子检测价格突破关键位（近期高低点）时成交量是否配合，若放量但价格未能持续（假突破），则反向交易。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class StoplossVolumeConfirmation(BaseFactor):
    """针对SL止损亏损（占比约40%），这些亏损往往伴随放量突破失败。该因子检测价格突破关键位（近期高低点）时成交量是否配合，若放量但价格未能持续（假突破），则反向交易。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sl_volume_confirm",
            name="StopLoss_Volume_Confirmation",
            display_name="止损量能确认",
            description="针对SL止损亏损（占比约40%），这些亏损往往伴随放量突破失败。该因子检测价格突破关键位（近期高低点）时成交量是否配合，若放量但价格未能持续（假突破），则反向交易。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        high = data['high']
        low = data['low']
        vol_ma = volume.rolling(20).mean()
        vol_ratio = volume / (vol_ma + 1e-9)
        range_20 = high.rolling(20).max() - low.rolling(20).min()
        pos = (close - low.rolling(20).min()) / (range_20 + 1e-9)
        ret_1 = close.pct_change()
        ret_5 = close.pct_change(5)
        breakout = ((close > high.rolling(20).max().shift(1)) | (close < low.rolling(20).min().shift(1))).astype(float)
        result = (-breakout * vol_ratio * (ret_5 - ret_1).abs()).clip(-1, 1)
        return result
