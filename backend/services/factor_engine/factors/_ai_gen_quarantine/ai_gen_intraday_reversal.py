"""AI因子: 日内反转强度 | 置信:60% | 通过当日K线形态（上影线、下影线、实体）判断可能的日内反转。当出现长上影线或大阴线时，表明多头被压制，后续容易亏损。因子值在出现反转信号时为负。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Intraday_Reversal_Strength(BaseFactor):
    """通过当日K线形态（上影线、下影线、实体）判断可能的日内反转。当出现长上影线或大阴线时，表明多头被压制，后续容易亏损。因子值在出现反转信号时为负。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_intraday_reversal",
            name="Intraday Reversal Strength",
            display_name="日内反转强度",
            description="通过当日K线形态（上影线、下影线、实体）判断可能的日内反转。当出现长上影线或大阴线时，表明多头被压制，后续容易亏损。因子值在出现反转信号时为负。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        open_ = data['open']
        high = data['high']
        low = data['low']
        close = data['close']
        # 计算上下影线占比
        upper_shadow = (high - np.maximum(open_, close)) / (high - low + 1e-10)
        lower_shadow = (np.minimum(open_, close) - low) / (high - low + 1e-10)
        body = np.abs(close - open_) / (high - low + 1e-10)
        # 长上影线且实体较小（十字星或上吊线），或大阴线（收盘远低于开盘）
        bearish_signal = ((upper_shadow > 0.6) & (body < 0.3)) | ((close < open_) & (body > 0.6))
        # 平滑处理
        bear_strength = bearish_signal.rolling(3).mean().fillna(0)
        result = 1 - 2 * bear_strength
        return result
