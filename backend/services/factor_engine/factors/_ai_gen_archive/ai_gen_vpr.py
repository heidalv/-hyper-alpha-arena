"""AI因子: 价格冲击因子 | 置信:60% | 衡量单位波动所对应的成交量，高值表示在较小波动下出现较大成交量，可能意味着流动性不足或订单失衡，容易触发止损或强制平仓。因子值越高，冲击风险越大。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePressure(BaseFactor):
    """衡量单位波动所对应的成交量，高值表示在较小波动下出现较大成交量，可能意味着流动性不足或订单失衡，容易触发止损或强制平仓。因子值越高，冲击风险越大。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vpr",
            name="Volume Pressure",
            display_name="价格冲击因子",
            description="衡量单位波动所对应的成交量，高值表示在较小波动下出现较大成交量，可能意味着流动性不足或订单失衡，容易触发止损或强制平仓。因子值越高，冲击风险越大。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算典型波动: (high-low)/close
        typical_range = (data['high'] - data['low']) / data['close']
        # 成交量标准化: 近期成交量均值
        vol_ma = data['volume'].rolling(10).mean()
        vol_ratio = data['volume'] / (vol_ma + 1e-10)
        # 价格冲击因子 = vol_ratio / (typical_range + 1e-10)
        impact = vol_ratio / (typical_range + 1e-10)
        # 滚动Z-score标准化
        result = (impact - impact.rolling(20).mean()) / (impact.rolling(20).std() + 1e-10)
        return result.clip(-1, 1).fillna(0)
