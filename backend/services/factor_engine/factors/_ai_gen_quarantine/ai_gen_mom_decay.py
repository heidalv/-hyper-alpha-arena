"""AI因子: 动量衰减因子 | 置信:60% | 结合价格位置与成交量萎缩，识别趋势衰竭。计算收盘价在N周期最高最低区间中的位置，并乘以成交量相对均值的萎缩程度。当价格处于极端且成交量萎缩时，预示趋势难以维持，易出现超时或反转，输出负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumDecay(BaseFactor):
    """结合价格位置与成交量萎缩，识别趋势衰竭。计算收盘价在N周期最高最低区间中的位置，并乘以成交量相对均值的萎缩程度。当价格处于极端且成交量萎缩时，预示趋势难以维持，易出现超时或反转，输出负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_decay",
            name="Momentum Decay",
            display_name="动量衰减因子",
            description="结合价格位置与成交量萎缩，识别趋势衰竭。计算收盘价在N周期最高最低区间中的位置，并乘以成交量相对均值的萎缩程度。当价格处于极端且成交量萎缩时，预示趋势难以维持，易出现超时或反转，输出负值。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high_20 = data['high'].rolling(20).max()
        low_20 = data['low'].rolling(20).min()
        pos = (close - low_20) / (high_20 - low_20 + 1e-10)
        volume = data['volume']
        vol_ma = volume.rolling(20).mean()
        vol_ratio = volume / vol_ma
        # 动量衰减信号：价格在极端且成交量萎缩
        decay = (pos - 0.5).abs() * 2  # 0~1
        signal = -decay * (1 - vol_ratio.clip(0, 1))
        return signal.fillna(0).clip(-1, 1)
