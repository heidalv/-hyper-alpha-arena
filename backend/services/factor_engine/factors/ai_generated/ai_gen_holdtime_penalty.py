"""AI因子: 持仓超时惩罚因子 | 置信:60% | 基于亏损模式中max_hold_timeout高频出现，设计因子惩罚长时间持仓且价格未创新高的标的。通过计算当前价格相对20日高点的距离与持仓时间（用成交量累积代理）的交互，识别可能因持仓过久导致亏损的标的。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HoldTimePenaltyScore(BaseFactor):
    """基于亏损模式中max_hold_timeout高频出现，设计因子惩罚长时间持仓且价格未创新高的标的。通过计算当前价格相对20日高点的距离与持仓时间（用成交量累积代理）的交互，识别可能因持仓过久导致亏损的标的。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_holdtime_penalty",
            name="Hold_Time_Penalty_Score",
            display_name="持仓超时惩罚因子",
            description="基于亏损模式中max_hold_timeout高频出现，设计因子惩罚长时间持仓且价格未创新高的标的。通过计算当前价格相对20日高点的距离与持仓时间（用成交量累积代理）的交互，识别可能因持仓过久导致亏损的标的。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(20)
        high_dist = (data['close'] - data['high'].rolling(20).max()) / (data['high'].rolling(20).max() + 1e-9)
        vol_accum = data['volume'].rolling(20).sum() / (data['volume'].rolling(20).mean() * 20 + 1e-9)
        result = (ret * high_dist * vol_accum).clip(-1, 1)
        return result
