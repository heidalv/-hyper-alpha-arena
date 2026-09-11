"""AI因子: 未知市场状态防御因子 | 置信:55% | 针对亏损集中在regime=unknown的情况，构建基于价格位置和波动率稳定性的防御因子。当价格处于中间区域且波动率不稳定（regime未知特征）时，因子值接近0避免交易；当价格处于极端区域且波动率稳定时，因子值偏向±1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeUnknownGuard(BaseFactor):
    """针对亏损集中在regime=unknown的情况，构建基于价格位置和波动率稳定性的防御因子。当价格处于中间区域且波动率不稳定（regime未知特征）时，因子值接近0避免交易；当价格处于极端区域且波动率稳定时，因子值偏向±1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_unknown_guard",
            name="regime_unknown_guard",
            display_name="未知市场状态防御因子",
            description="针对亏损集中在regime=unknown的情况，构建基于价格位置和波动率稳定性的防御因子。当价格处于中间区域且波动率不稳定（regime未知特征）时，因子值接近0避免交易；当价格处于极端区域且波动率稳定时，因子值偏向±1。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        price_range = (data['high'].rolling(20).max() - data['low'].rolling(20).min()) / (data['close'] + 1e-9)
        vol_ratio = data['close'].pct_change().rolling(5).std() / (data['close'].pct_change().rolling(20).std() + 1e-9)
        mid_pos = (data['close'] - data['low'].rolling(20).min()) / (data['high'].rolling(20).max() - data['low'].rolling(20).min() + 1e-9)
        result = (mid_pos - 0.5) * 2 * (1 - abs(vol_ratio - 1)).clip(0, 1)
        result = result.where(price_range > 0.05, 0.0)
        result = result.clip(-1, 1)
        return result
