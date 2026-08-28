"""AI因子: 止损突破距离因子 | 置信:58% | 亏损模式中sl止损频繁，且regime=unknown。该因子衡量价格在近期高/低点附近的突破力度与距离，当价格快速逼近极端位置但未确认趋势时，容易触发止损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class StopLossBreakoutDistance(BaseFactor):
    """亏损模式中sl止损频繁，且regime=unknown。该因子衡量价格在近期高/低点附近的突破力度与距离，当价格快速逼近极端位置但未确认趋势时，容易触发止损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sl_break",
            name="stop_loss_breakout_distance",
            display_name="止损突破距离因子",
            description="亏损模式中sl止损频繁，且regime=unknown。该因子衡量价格在近期高/低点附近的突破力度与距离，当价格快速逼近极端位置但未确认趋势时，容易触发止损。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high_20 = data['high'].rolling(20).max()
        low_20 = data['low'].rolling(20).min()
        mid = (high_20 + low_20) / 2
        dist_high = (data['close'] - high_20) / (high_20 - low_20 + 1e-9)
        dist_low = (low_20 - data['close']) / (high_20 - low_20 + 1e-9)
        range_ratio = (data['high'] - data['low']) / (high_20 - low_20 + 1e-9)
        result = ((dist_high - dist_low) * range_ratio).clip(-1, 1)
        return result
