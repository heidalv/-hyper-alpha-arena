"""AI因子: 突破回踩动量 | 置信:60% | 捕捉价格突破后回踩再启动的动量，避免在突破后立即追高导致止损，同时过滤掉无趋势的震荡。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Breakoutpullbackmomentum(BaseFactor):
    """捕捉价格突破后回踩再启动的动量，避免在突破后立即追高导致止损，同时过滤掉无趋势的震荡。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_breakout_pullback",
            name="BreakoutPullbackMomentum",
            display_name="突破回踩动量",
            description="捕捉价格突破后回踩再启动的动量，避免在突破后立即追高导致止损，同时过滤掉无趋势的震荡。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret_short = data['close'].pct_change(3)
        ret_long = data['close'].pct_change(10)
        high_prev = data['high'].shift(1).rolling(10).max()
        low_prev = data['low'].shift(1).rolling(10).min()
        breakout = (data['close'] > high_prev).astype(float) - (data['close'] < low_prev).astype(float)
        pullback = ((data['close'] - data['low']) / (data['high'] - data['low']).replace(0, 1e-9) - 0.5) * 2
        momentum = (ret_short - ret_long).clip(-1, 1)
        result = (breakout * 0.5 + pullback * 0.3 + momentum * 0.2).clip(-1, 1)
        return result
