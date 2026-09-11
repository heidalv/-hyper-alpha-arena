"""AI因子: 主策略强平突破因子 | 置信:55% | 基于master_running_close亏损模式，捕捉价格快速突破关键位后反向运行的风险。通过计算近期价格突破幅度与反转强度，识别可能触发强制平仓的极端波动区间。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Masterclosebreak(BaseFactor):
    """基于master_running_close亏损模式，捕捉价格快速突破关键位后反向运行的风险。通过计算近期价格突破幅度与反转强度，识别可能触发强制平仓的极端波动区间。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_master_close_break",
            name="MasterCloseBreak",
            display_name="主策略强平突破因子",
            description="基于master_running_close亏损模式，捕捉价格快速突破关键位后反向运行的风险。通过计算近期价格突破幅度与反转强度，识别可能触发强制平仓的极端波动区间。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high_break = data['high'] / data['close'].rolling(10).max() - 1
        low_break = data['low'] / data['close'].rolling(10).min() - 1
        reversal = (data['close'].pct_change(3) * -1).rolling(5).mean()
        result = ((high_break + low_break) * reversal).clip(-1, 1)
        return result
