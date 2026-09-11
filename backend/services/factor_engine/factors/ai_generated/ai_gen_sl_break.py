"""AI因子: 止损突破强度因子 | 置信:58% | 针对sl亏损模式，识别价格突破近期支撑/阻力后的惯性强度。当价格突破且伴随放量时，因子值向-1偏移，表示止损触发后趋势延续风险高，避免逆势加仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class StoplossBreakoutIntensity(BaseFactor):
    """针对sl亏损模式，识别价格突破近期支撑/阻力后的惯性强度。当价格突破且伴随放量时，因子值向-1偏移，表示止损触发后趋势延续风险高，避免逆势加仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sl_break",
            name="StopLoss_Breakout_Intensity",
            display_name="止损突破强度因子",
            description="针对sl亏损模式，识别价格突破近期支撑/阻力后的惯性强度。当价格突破且伴随放量时，因子值向-1偏移，表示止损触发后趋势延续风险高，避免逆势加仓。",
            category="behavioral",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high_20 = data['high'].rolling(20).max()
        low_20 = data['low'].rolling(20).min()
        mid = (high_20 + low_20) / 2
        price_pos = (data['close'] - mid) / (high_20 - low_20 + 1e-9)
        vol_ratio = data['volume'] / (data['volume'].rolling(20).mean() + 1e-9)
        result = (price_pos * 2 - 1) * (vol_ratio - 1).clip(-0.5, 0.5) * 2
        result = result.clip(-1, 1)
        return result
