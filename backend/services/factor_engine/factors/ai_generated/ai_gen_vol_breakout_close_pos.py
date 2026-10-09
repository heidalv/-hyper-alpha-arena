"""AI因子: 波动突破收盘位置 | 置信:58% | 收盘价在近20日高低区间中的相对位置，结合波动率突变（当前振幅/20日均振幅）确认突破有效性。收盘位置高且波动放大表示买方主导突破，未来上涨概率高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityBreakoutClosePosition(BaseFactor):
    """收盘价在近20日高低区间中的相对位置，结合波动率突变（当前振幅/20日均振幅）确认突破有效性。收盘位置高且波动放大表示买方主导突破，未来上涨概率高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_breakout_close_pos",
            name="Volatility Breakout Close Position",
            display_name="波动突破收盘位置",
            description="收盘价在近20日高低区间中的相对位置，结合波动率突变（当前振幅/20日均振幅）确认突破有效性。收盘位置高且波动放大表示买方主导突破，未来上涨概率高。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        hi = data['high'].rolling(20).max()
        lo = data['low'].rolling(20).min()
        pos = (data['close'] - lo) / (hi - lo + 1e-9)
        rng = (data['high'] - data['low']) / (data['close'] + 1e-9)
        vr = rng / (rng.rolling(20).mean() + 1e-9)
        result = ((pos - 0.5) * 2 * vr.clip(0, 3)).clip(-1, 1)
        return result
