"""AI因子: 趋势持续时间衰竭 | 置信:60% | 统计价格沿EMA方向持续运行的K线根数，结合MACD动能柱衰减，识别趋势老化。max_hold_timeout常因趋势运行过久而衰竭反转，此因子在趋势持续多日且动能衰竭时给出反向信号。正值看多（下跌衰竭），负值看空（上涨衰竭）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendDurationExhaustion(BaseFactor):
    """统计价格沿EMA方向持续运行的K线根数，结合MACD动能柱衰减，识别趋势老化。max_hold_timeout常因趋势运行过久而衰竭反转，此因子在趋势持续多日且动能衰竭时给出反向信号。正值看多（下跌衰竭），负值看空（上涨衰竭）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tde",
            name="Trend Duration Exhaustion",
            display_name="趋势持续时间衰竭",
            description="统计价格沿EMA方向持续运行的K线根数，结合MACD动能柱衰减，识别趋势老化。max_hold_timeout常因趋势运行过久而衰竭反转，此因子在趋势持续多日且动能衰竭时给出反向信号。正值看多（下跌衰竭），负值看空（上涨衰竭）。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        # EMA trend direction
        ema20 = close.ewm(span=20, adjust=False).mean()
        ema_slope = ema20.diff(5)
        direction = ema_slope.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        # Count consecutive same direction
        streak = direction.groupby((direction != direction.shift()).cumsum()).cumcount() + 1
        streak = streak.where(direction != 0, 0)
        # MACD histogram
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - signal_line
        hist_max20 = macd_hist.abs().rolling(20).max()
        hist_ratio = macd_hist.abs() / hist_max20.replace(0, 1e-9)
        # Exhaustion: streak >= 5 and hist_ratio declining below 0.5
        exhaustion_condition = (streak >= 5) & (hist_ratio < 0.5)
        # Signal: opposite to direction, scaled by hist_ratio
        raw_signal = -direction * (1.0 - hist_ratio)
        result = raw_signal * exhaustion_condition.astype(float)
        result = result.clip(-1.0, 1.0)
        return result
