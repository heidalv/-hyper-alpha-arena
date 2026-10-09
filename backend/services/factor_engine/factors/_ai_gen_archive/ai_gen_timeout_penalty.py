"""AI因子: 持仓超时惩罚信号 | 置信:55% | 基于max_hold_timeout亏损模式，识别持仓时间过长且价格停滞的品种。使用价格变化率与成交量衰减的比值，当价格长时间无进展且量能萎缩时给出负向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TimeoutPenaltySignal(BaseFactor):
    """基于max_hold_timeout亏损模式，识别持仓时间过长且价格停滞的品种。使用价格变化率与成交量衰减的比值，当价格长时间无进展且量能萎缩时给出负向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timeout_penalty",
            name="Timeout_penalty_signal",
            display_name="持仓超时惩罚信号",
            description="基于max_hold_timeout亏损模式，识别持仓时间过长且价格停滞的品种。使用价格变化率与成交量衰减的比值，当价格长时间无进展且量能萎缩时给出负向信号。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(20)
        vol_ratio = data['volume'].rolling(20).mean() / (data['volume'].rolling(50).mean() + 1e-9)
        result = (-ret * (1 - vol_ratio)).clip(-1, 1)
        return result
