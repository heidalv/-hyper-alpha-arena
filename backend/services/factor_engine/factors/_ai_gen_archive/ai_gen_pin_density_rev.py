"""AI因子: 插针密度加权反转 | 置信:60% | 先计算插针密度(影线/实体比值的20日均值)作为波动环境分位，再用其加权影线不对称度。高插针密度环境下影线信号更可靠，因子在震荡插针行情中给出更强的反转方向预测。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityWeightedReversal(BaseFactor):
    """先计算插针密度(影线/实体比值的20日均值)作为波动环境分位，再用其加权影线不对称度。高插针密度环境下影线信号更可靠，因子在震荡插针行情中给出更强的反转方向预测。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_rev",
            name="Pin Density Weighted Reversal",
            display_name="插针密度加权反转",
            description="先计算插针密度(影线/实体比值的20日均值)作为波动环境分位，再用其加权影线不对称度。高插针密度环境下影线信号更可靠，因子在震荡插针行情中给出更强的反转方向预测。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        asym = (lower - upper) / body
        dens = (data[['open','close']].max(axis=1) - data[['open','close']].min(axis=1) + 1e-9)
        pin = (data['high'] - data['low']) / dens
        result = (asym * pin.rolling(20).mean()).rolling(3).mean().clip(-1, 1)
        return result
