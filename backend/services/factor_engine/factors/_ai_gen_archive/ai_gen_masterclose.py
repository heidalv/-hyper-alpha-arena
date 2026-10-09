"""AI因子: 主账户平仓量能异动 | 置信:60% | 亏损模式中master_running_close出现在VIRTUAL和BNB，亏损幅度-1.82%和-3.05%，且次数较少（x2和x1），表明在特定时刻出现主动平仓信号，可能与成交量异常放大有关。因子通过成交量短期激增与价格动量结合，在量能异动时给出反向信号，避免追涨杀跌。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MasterCloseVolumesurge(BaseFactor):
    """亏损模式中master_running_close出现在VIRTUAL和BNB，亏损幅度-1.82%和-3.05%，且次数较少（x2和x1），表明在特定时刻出现主动平仓信号，可能与成交量异常放大有关。因子通过成交量短期激增与价格动量结合，在量能异动时给出反向信号，避免追涨杀跌。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_masterclose",
            name="Master_Close_VolumeSurge",
            display_name="主账户平仓量能异动",
            description="亏损模式中master_running_close出现在VIRTUAL和BNB，亏损幅度-1.82%和-3.05%，且次数较少（x2和x1），表明在特定时刻出现主动平仓信号，可能与成交量异常放大有关。因子通过成交量短期激增与价格动量结合，在量能异动时给出反向信号，避免追涨杀跌。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        vol_ma = data['volume'].rolling(20).mean()
        vol_ratio = data['volume'] / (vol_ma + 1e-9)
        ret = data['close'].pct_change(3)
        result = (-vol_ratio * ret).clip(-1, 1)
        return result
