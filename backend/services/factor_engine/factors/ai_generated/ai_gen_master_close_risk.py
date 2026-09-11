"""AI因子: 主账户强平风险因子 | 置信:50% | 针对master_running_close导致的亏损，识别价格快速脱离成本区且波动放大的情况。使用短期动量与波动率扩张的乘积，当波动率快速上升且动量衰竭时给出负向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MasterCloseRisk(BaseFactor):
    """针对master_running_close导致的亏损，识别价格快速脱离成本区且波动放大的情况。使用短期动量与波动率扩张的乘积，当波动率快速上升且动量衰竭时给出负向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_master_close_risk",
            name="Master_close_risk",
            display_name="主账户强平风险因子",
            description="针对master_running_close导致的亏损，识别价格快速脱离成本区且波动放大的情况。使用短期动量与波动率扩张的乘积，当波动率快速上升且动量衰竭时给出负向信号。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret_short = data['close'].pct_change(3)
        vol_short = data['close'].pct_change().rolling(3).std()
        vol_long = data['close'].pct_change().rolling(20).std()
        vol_expand = vol_short / (vol_long + 1e-9)
        result = (-(ret_short * vol_expand)).clip(-1, 1)
        return result
