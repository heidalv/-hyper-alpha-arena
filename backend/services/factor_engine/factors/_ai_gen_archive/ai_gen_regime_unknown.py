"""AI因子: 未知状态量价混乱度 | 置信:65% | 针对regime=unknown的普遍亏损：未知状态常因量价关系不清晰（如放量但价格无方向）。该因子通过成交量变异系数与价格方向稳定性的负相关，捕捉市场混沌状态，值越低（接近-1）表示越混乱，应减少趋势交易。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeUnknownVolumeConfusion(BaseFactor):
    """针对regime=unknown的普遍亏损：未知状态常因量价关系不清晰（如放量但价格无方向）。该因子通过成交量变异系数与价格方向稳定性的负相关，捕捉市场混沌状态，值越低（接近-1）表示越混乱，应减少趋势交易。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_unknown",
            name="Regime_Unknown_Volume_Confusion",
            display_name="未知状态量价混乱度",
            description="针对regime=unknown的普遍亏损：未知状态常因量价关系不清晰（如放量但价格无方向）。该因子通过成交量变异系数与价格方向稳定性的负相关，捕捉市场混沌状态，值越低（接近-1）表示越混乱，应减少趋势交易。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        vol_std = data['volume'].rolling(20).std()
        vol_mean = data['volume'].rolling(20).mean()
        cv = vol_std / (vol_mean + 1e-9)
        price_dir = (data['close'].diff() > 0).astype(float).rolling(20).mean()
        confusion = 1 - (price_dir - 0.5).abs() * 2
        result = (cv - confusion).clip(-1, 1)
        return result
