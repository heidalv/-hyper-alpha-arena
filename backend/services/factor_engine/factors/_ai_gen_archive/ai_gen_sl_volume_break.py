"""AI因子: 止损放量突破过滤因子 | 置信:45% | SL亏损集中在UNI/XPL/VIRTUAL/SOL，且亏损幅度较大（-1.3%以上），结合regime=unknown，推测是放量突破假信号。本因子在放量突破时给出负向信号（避免追突破），在缩量回调时给出正向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Stoplossvolumebreakoutfilter(BaseFactor):
    """SL亏损集中在UNI/XPL/VIRTUAL/SOL，且亏损幅度较大（-1.3%以上），结合regime=unknown，推测是放量突破假信号。本因子在放量突破时给出负向信号（避免追突破），在缩量回调时给出正向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sl_volume_break",
            name="StopLossVolumeBreakoutFilter",
            display_name="止损放量突破过滤因子",
            description="SL亏损集中在UNI/XPL/VIRTUAL/SOL，且亏损幅度较大（-1.3%以上），结合regime=unknown，推测是放量突破假信号。本因子在放量突破时给出负向信号（避免追突破），在缩量回调时给出正向信号。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        vol_ratio = data['volume'] / (data['volume'].rolling(20).mean() + 1e-9)
        ret = data['close'].pct_change(5)
        result = (ret - vol_ratio * 0.5).clip(-1, 1)
        return result
