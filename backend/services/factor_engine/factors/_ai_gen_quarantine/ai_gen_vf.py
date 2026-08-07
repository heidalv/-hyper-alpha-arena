"""AI因子: 量能衰竭因子 | 置信:60% | 当成交量放大但价格实体占比缩小（长影线）时，多空分歧加剧，趋势难以为继，常见于master_running_close亏损前。负值表示放量滞涨/滞跌，应减仓；正值表示量价健康。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeExhaustionFactor(BaseFactor):
    """当成交量放大但价格实体占比缩小（长影线）时，多空分歧加剧，趋势难以为继，常见于master_running_close亏损前。负值表示放量滞涨/滞跌，应减仓；正值表示量价健康。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vf",
            name="Volume Exhaustion Factor",
            display_name="量能衰竭因子",
            description="当成交量放大但价格实体占比缩小（长影线）时，多空分歧加剧，趋势难以为继，常见于master_running_close亏损前。负值表示放量滞涨/滞跌，应减仓；正值表示量价健康。",
            category="volume",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        open, high, low, close, volume = data['open'], data['high'], data['low'], data['close'], data['volume']
        body = (close - open).abs()
        wick = high - low + 1e-8
        body_ratio = body / wick
        vol_ma = volume.rolling(20).mean()
        vol_z = (volume - vol_ma) / (vol_ma + 1e-8)
        exhaustion = -vol_z * (1 - body_ratio)
        norm = exhaustion.rolling(50).std() + 1e-8
        result = exhaustion / norm
        result = result.fillna(0).clip(-2, 2) / 2
        return result.clip(-1, 1)
