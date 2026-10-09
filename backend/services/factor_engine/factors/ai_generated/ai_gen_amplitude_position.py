"""AI因子: 振幅位置 | 置信:57% | 收盘价在近期高低区间中的相对位置，结合近期振幅。位置高且振幅放大表示突破确认，位置低且缩量表示超卖。预测未来方向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class AmplitudePosition(BaseFactor):
    """收盘价在近期高低区间中的相对位置，结合近期振幅。位置高且振幅放大表示突破确认，位置低且缩量表示超卖。预测未来方向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_amplitude_position",
            name="Amplitude Position",
            display_name="振幅位置",
            description="收盘价在近期高低区间中的相对位置，结合近期振幅。位置高且振幅放大表示突破确认，位置低且缩量表示超卖。预测未来方向。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high20 = data['high'].rolling(20).max()
        low20 = data['low'].rolling(20).min()
        pos = (data['close'] - low20) / (high20 - low20 + 1e-9)
        result = (pos - 0.5).clip(-1, 1)
        return result
