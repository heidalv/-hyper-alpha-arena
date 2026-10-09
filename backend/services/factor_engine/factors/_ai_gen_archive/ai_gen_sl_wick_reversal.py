"""AI因子: 止损影线反转 | 置信:58% | 针对sl止损亏损模式：止损触发常伴随长影线（价格瞬间穿越止损位后回落）。该因子通过上下影线比例和收盘位置，识别假突破形态，值越高表示影线越长、收盘越弱，越可能触发止损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class SLWickReversal(BaseFactor):
    """针对sl止损亏损模式：止损触发常伴随长影线（价格瞬间穿越止损位后回落）。该因子通过上下影线比例和收盘位置，识别假突破形态，值越高表示影线越长、收盘越弱，越可能触发止损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sl_wick_reversal",
            name="SL_Wick_Reversal",
            display_name="止损影线反转",
            description="针对sl止损亏损模式：止损触发常伴随长影线（价格瞬间穿越止损位后回落）。该因子通过上下影线比例和收盘位置，识别假突破形态，值越高表示影线越长、收盘越弱，越可能触发止损。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        upper_wick = data['high'] - data[['open', 'close']].max(axis=1)
        lower_wick = data[['open', 'close']].min(axis=1) - data['low']
        body = (data['close'] - data['open']).abs()
        total_range = data['high'] - data['low'] + 1e-9
        wick_ratio = (upper_wick - lower_wick) / total_range
        close_pos = (data['close'] - data['low']) / total_range
        body_ratio = body / total_range
        result = (wick_ratio * 0.5 + (0.5 - close_pos) * 0.3 + (1 - body_ratio) * 0.2) * 2
        result = result.clip(-1, 1)
        return result
