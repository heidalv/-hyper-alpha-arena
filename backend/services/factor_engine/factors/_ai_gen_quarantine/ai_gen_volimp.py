"""AI因子: 成交量冲击因子 | 置信:60% | 衡量价格变动与成交量放大程度的匹配度。当价格突破伴随异常放量时为看多信号，无量突破视为假突破给予负值。用于过滤伪趋势和噪声。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeImpact(BaseFactor):
    """衡量价格变动与成交量放大程度的匹配度。当价格突破伴随异常放量时为看多信号，无量突破视为假突破给予负值。用于过滤伪趋势和噪声。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volimp",
            name="VolumeImpact",
            display_name="成交量冲击因子",
            description="衡量价格变动与成交量放大程度的匹配度。当价格突破伴随异常放量时为看多信号，无量突破视为假突破给予负值。用于过滤伪趋势和噪声。",
            category="volume",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        # 价格变化率
        ret = close.pct_change()
        # 成交量变化率（相对20日均值）
        vol_ma20 = volume.rolling(20).mean()
        vol_ratio = volume / vol_ma20 - 1
        # 定义价格突破：当前收盘价高于前10日最高价（上涨突破）或低于前10日最低价（下跌突破）
        high10 = close.rolling(10).max().shift(1)
        low10 = close.rolling(10).min().shift(1)
        up_break = (close > high10).astype(float)
        down_break = (close < low10).astype(float)
        # 信号：上涨突破且放量则正，下跌突破且放量则负，否则中性
        signal = (up_break * np.sign(ret) * np.clip(vol_ratio, 0, 1)) + (down_break * np.sign(ret) * np.clip(vol_ratio, 0, 1))
        result = np.clip(signal, -1, 1)
        return result
