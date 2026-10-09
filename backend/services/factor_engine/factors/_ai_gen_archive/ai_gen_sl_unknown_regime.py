"""AI因子: 未知市场状态止损规避因子 | 置信:50% | 针对止损亏损（sl）在regime=unknown时高频出现（UNI、VIRTUAL、XPL），未知状态往往伴随高波动和趋势不明确。因子结合价格突破幅度与成交量异常放大，识别高风险做空窗口，输出负向信号（看多）以规避。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Slunknownregimeavoid(BaseFactor):
    """针对止损亏损（sl）在regime=unknown时高频出现（UNI、VIRTUAL、XPL），未知状态往往伴随高波动和趋势不明确。因子结合价格突破幅度与成交量异常放大，识别高风险做空窗口，输出负向信号（看多）以规避。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sl_unknown_regime",
            name="SLUnknownRegimeAvoid",
            display_name="未知市场状态止损规避因子",
            description="针对止损亏损（sl）在regime=unknown时高频出现（UNI、VIRTUAL、XPL），未知状态往往伴随高波动和趋势不明确。因子结合价格突破幅度与成交量异常放大，识别高风险做空窗口，输出负向信号（看多）以规避。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret5 = data['close'].pct_change(5)
        vol_ratio = data['volume'] / (data['volume'].rolling(20).mean() + 1e-9)
        atr = (data['high'] - data['low']).rolling(14).mean()
        norm_atr = atr / (data['close'] + 1e-9)
        result = ((ret5 * -2) + (vol_ratio - 1) * 3 - norm_atr * 20).clip(-1, 1)
        return result
