"""AI因子: 止损与超时组合风险因子 | 置信:45% | 结合sl和max_hold_timeout两种亏损模式，识别价格在持仓期内先出现不利波动但未触发止损、最终超时亏损的情况。通过衡量价格回撤深度与恢复速度的比值，当回撤深且恢复慢时给出负向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Sltimeoutcombo(BaseFactor):
    """结合sl和max_hold_timeout两种亏损模式，识别价格在持仓期内先出现不利波动但未触发止损、最终超时亏损的情况。通过衡量价格回撤深度与恢复速度的比值，当回撤深且恢复慢时给出负向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sl_timeout_combo",
            name="SLTimeoutCombo",
            display_name="止损与超时组合风险因子",
            description="结合sl和max_hold_timeout两种亏损模式，识别价格在持仓期内先出现不利波动但未触发止损、最终超时亏损的情况。通过衡量价格回撤深度与恢复速度的比值，当回撤深且恢复慢时给出负向信号。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(10)
        max_dd = (data['close'] / data['close'].rolling(10).max() - 1).rolling(10).min()
        recovery = (data['close'] / data['close'].rolling(10).max()).rolling(5).mean()
        result = -1 * (abs(max_dd) * (1 - recovery) * (1 + abs(ret))).clip(-1, 1)
        return result
