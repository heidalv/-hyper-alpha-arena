"""AI因子: 空头止损与未知状态 | 置信:55% | UNI和VIRTUAL空头止损亏损多次出现，且regime=unknown。该因子识别价格在近期出现急跌后快速反弹（V型反转）的形态，这种形态容易触发空头止损。计算最近5日最低点到当前收盘的反弹幅度与前期下跌幅度的比值，值越接近+1表示反弹越强，空头止损风险越高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortStoplossRegimeuncertainty(BaseFactor):
    """UNI和VIRTUAL空头止损亏损多次出现，且regime=unknown。该因子识别价格在近期出现急跌后快速反弹（V型反转）的形态，这种形态容易触发空头止损。计算最近5日最低点到当前收盘的反弹幅度与前期下跌幅度的比值，值越接近+1表示反弹越强，空头止损风险越高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_short_sl_regime_unknown",
            name="Short_StopLoss_RegimeUncertainty",
            display_name="空头止损与未知状态",
            description="UNI和VIRTUAL空头止损亏损多次出现，且regime=unknown。该因子识别价格在近期出现急跌后快速反弹（V型反转）的形态，这种形态容易触发空头止损。计算最近5日最低点到当前收盘的反弹幅度与前期下跌幅度的比值，值越接近+1表示反弹越强，空头止损风险越高。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        low5 = data['low'].rolling(5).min()
        high5 = data['high'].rolling(5).max()
        drop = high5 - low5
        rebound = data['close'] - low5
        ratio = rebound / (drop + 1e-9)
        result = (ratio * 2 - 1).clip(-1, 1)
        return result.fillna(0)
