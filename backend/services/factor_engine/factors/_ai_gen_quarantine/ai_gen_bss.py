"""AI因子: 空头强度信号 | 置信:50% | 通过连续阴线次数和下跌成交量强度判断空头压力。当连续下跌天数超过阈值且下跌日成交量大于平均水平时，信号为-1（不宜做多）；反之为+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Bearish_Strength_Signal(BaseFactor):
    """通过连续阴线次数和下跌成交量强度判断空头压力。当连续下跌天数超过阈值且下跌日成交量大于平均水平时，信号为-1（不宜做多）；反之为+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bss",
            name="Bearish Strength Signal",
            display_name="空头强度信号",
            description="通过连续阴线次数和下跌成交量强度判断空头压力。当连续下跌天数超过阈值且下跌日成交量大于平均水平时，信号为-1（不宜做多）；反之为+1。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        # 连续阴线（收盘低于开盘）
        bearish = (close < data['open']).astype(int)
        consecutive_bear = bearish.groupby((bearish != bearish.shift()).cumsum()).cumsum()
        # 下跌日成交量均值
        vol_ratio = volume / volume.rolling(20).mean()
        strength = (consecutive_bear >= 3) & (vol_ratio > 1.2)
        # 转化为-1（空头强）到+1（多头强）
        result = -strength.astype(int) * 2 + 1  # True-> -1, False-> +1
        return result.astype(float).fillna(0).clip(-1,1)
