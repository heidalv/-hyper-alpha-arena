"""AI因子: 持仓超时惩罚因子 | 置信:62% | 识别因max_hold_timeout导致的亏损模式：当价格在持仓期间横盘或小幅反向波动，且成交量萎缩时，持仓时间越长亏损概率越大。该因子通过计算近期价格路径效率（收盘价净变动/累计绝对变动）与成交量变化率的负相关来捕捉这种'时间损耗'效应。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Holdtimepenalty(BaseFactor):
    """识别因max_hold_timeout导致的亏损模式：当价格在持仓期间横盘或小幅反向波动，且成交量萎缩时，持仓时间越长亏损概率越大。该因子通过计算近期价格路径效率（收盘价净变动/累计绝对变动）与成交量变化率的负相关来捕捉这种'时间损耗'效应。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_holdtime_penalty",
            name="HoldTimePenalty",
            display_name="持仓超时惩罚因子",
            description="识别因max_hold_timeout导致的亏损模式：当价格在持仓期间横盘或小幅反向波动，且成交量萎缩时，持仓时间越长亏损概率越大。该因子通过计算近期价格路径效率（收盘价净变动/累计绝对变动）与成交量变化率的负相关来捕捉这种'时间损耗'效应。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(20)
        path = (data['close'].diff().abs().rolling(20).sum())
        efficiency = ret / (path + 1e-9)
        vol_ratio = data['volume'].rolling(5).mean() / (data['volume'].rolling(20).mean() + 1e-9)
        result = (efficiency * (1 - vol_ratio)).clip(-1, 1)
        return result
