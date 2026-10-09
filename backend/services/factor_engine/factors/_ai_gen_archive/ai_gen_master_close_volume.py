"""AI因子: 主控平仓量能 | 置信:50% | 针对master_running_close亏损：大额平仓往往伴随成交量异常放大和价格快速反转。用成交量短期激增与价格动量背离来识别，当放量但价格未创新高时偏向-1（看空）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MasterCloseVolumeSpike(BaseFactor):
    """针对master_running_close亏损：大额平仓往往伴随成交量异常放大和价格快速反转。用成交量短期激增与价格动量背离来识别，当放量但价格未创新高时偏向-1（看空）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_master_close_volume",
            name="Master_Close_Volume_Spike",
            display_name="主控平仓量能",
            description="针对master_running_close亏损：大额平仓往往伴随成交量异常放大和价格快速反转。用成交量短期激增与价格动量背离来识别，当放量但价格未创新高时偏向-1（看空）。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        vol_ma5 = data['volume'].rolling(5).mean()
        vol_ma20 = data['volume'].rolling(20).mean()
        vol_spike = (vol_ma5 / (vol_ma20 + 1e-9)) - 1
        ret5 = data['close'].pct_change(5)
        high20 = data['high'].rolling(20).max()
        near_high = (data['close'] / (high20 + 1e-9)) - 1
        result = -1 * vol_spike * (1 + near_high).clip(0, 2)
        result = result.clip(-1, 1)
        return result.fillna(0)
