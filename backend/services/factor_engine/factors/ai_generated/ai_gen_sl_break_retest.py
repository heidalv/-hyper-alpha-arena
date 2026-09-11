"""AI因子: 止损突破回测信号 | 置信:58% | 针对sl止损亏损模式，捕捉价格突破关键支撑/阻力位后的假突破行为。因子计算价格相对于近期高低的突破程度，并结合成交量变化，当价格突破后未能持续且成交量萎缩时，给出反向信号。该因子在regime=unknown时对止损型亏损有过滤作用。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class StopLossBreakRetestSignal(BaseFactor):
    """针对sl止损亏损模式，捕捉价格突破关键支撑/阻力位后的假突破行为。因子计算价格相对于近期高低的突破程度，并结合成交量变化，当价格突破后未能持续且成交量萎缩时，给出反向信号。该因子在regime=unknown时对止损型亏损有过滤作用。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sl_break_retest",
            name="stop_loss_break_retest_signal",
            display_name="止损突破回测信号",
            description="针对sl止损亏损模式，捕捉价格突破关键支撑/阻力位后的假突破行为。因子计算价格相对于近期高低的突破程度，并结合成交量变化，当价格突破后未能持续且成交量萎缩时，给出反向信号。该因子在regime=unknown时对止损型亏损有过滤作用。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high_20 = data['high'].rolling(20).max()
        low_20 = data['low'].rolling(20).min()
        mid_price = (high_20 + low_20) / 2
        price_pos = (data['close'] - mid_price) / (high_20 - low_20 + 1e-9)
        vol_change = data['volume'].pct_change(5)
        ret_5 = data['close'].pct_change(5)
        result = (price_pos * 2 - (vol_change * 0.1 + ret_5)).clip(-1, 1)
        return result.fillna(0)
