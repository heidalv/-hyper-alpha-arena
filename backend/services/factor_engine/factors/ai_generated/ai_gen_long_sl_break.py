"""AI因子: 做多止损突破风险 | 置信:65% | 针对做多单因止损亏损的问题，检测价格是否处于下跌趋势中的反弹失败形态。当价格跌破短期支撑且反弹无力时，做多风险高，因子值趋近-1；当价格站上短期均线且量能配合时，因子值趋近+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LongStoplossBreak(BaseFactor):
    """针对做多单因止损亏损的问题，检测价格是否处于下跌趋势中的反弹失败形态。当价格跌破短期支撑且反弹无力时，做多风险高，因子值趋近-1；当价格站上短期均线且量能配合时，因子值趋近+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_long_sl_break",
            name="Long_StopLoss_Break",
            display_name="做多止损突破风险",
            description="针对做多单因止损亏损的问题，检测价格是否处于下跌趋势中的反弹失败形态。当价格跌破短期支撑且反弹无力时，做多风险高，因子值趋近-1；当价格站上短期均线且量能配合时，因子值趋近+1。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_ma = data['close'].rolling(5).mean()
        long_ma = data['close'].rolling(20).mean()
        vol = data['volume'].rolling(5).mean() / (data['volume'].rolling(20).mean() + 1e-9)
        price_pos = (data['close'] - data['low'].rolling(10).min()) / (data['high'].rolling(10).max() - data['low'].rolling(10).min() + 1e-9)
        result = ((short_ma - long_ma) / (long_ma + 1e-9) * 5 + price_pos * 2 - vol * 0.5).clip(-1, 1)
        return result
