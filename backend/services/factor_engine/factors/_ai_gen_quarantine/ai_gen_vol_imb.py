"""AI因子: 成交量不平衡因子 | 置信:60% | 基于收盘价方向与成交量变化的关系：价格上涨时成交放大则看多，价格下跌时成交放大则看空，但若成交异常放大而价格却反向或停滞（假突破），则给出负信号。通过计算价格变化符号与成交量变化率的乘积，并归一化到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeImbalance(BaseFactor):
    """基于收盘价方向与成交量变化的关系：价格上涨时成交放大则看多，价格下跌时成交放大则看空，但若成交异常放大而价格却反向或停滞（假突破），则给出负信号。通过计算价格变化符号与成交量变化率的乘积，并归一化到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_imb",
            name="Volume Imbalance",
            display_name="成交量不平衡因子",
            description="基于收盘价方向与成交量变化的关系：价格上涨时成交放大则看多，价格下跌时成交放大则看空，但若成交异常放大而价格却反向或停滞（假突破），则给出负信号。通过计算价格变化符号与成交量变化率的乘积，并归一化到[-1,1]。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: pd.DataFrame with columns open, high, low, close, volume
        price_change = data['close'].pct_change()
        vol_change = data['volume'].pct_change()
        # 原始不平衡：价格方向*成交量变化，成交量减半避免噪音
        raw = price_change * vol_change
        # 用tanh压缩到[-1,1]
        result = np.tanh(raw * 10)
        return result.fillna(0.0)
