"""AI因子: 市场状态不确定性因子 | 置信:50% | 针对regime=unknown导致的亏损，设计因子捕捉市场状态不明确时的风险。通过比较短期和长期波动率及价格位置，当波动率结构异常（短期波动高于长期但价格无明确趋势）时发出负向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Regimeuncertainty(BaseFactor):
    """针对regime=unknown导致的亏损，设计因子捕捉市场状态不明确时的风险。通过比较短期和长期波动率及价格位置，当波动率结构异常（短期波动高于长期但价格无明确趋势）时发出负向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_uncertainty",
            name="RegimeUncertainty",
            display_name="市场状态不确定性因子",
            description="针对regime=unknown导致的亏损，设计因子捕捉市场状态不明确时的风险。通过比较短期和长期波动率及价格位置，当波动率结构异常（短期波动高于长期但价格无明确趋势）时发出负向信号。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_vol = data['close'].pct_change().rolling(5).std()
        long_vol = data['close'].pct_change().rolling(20).std()
        vol_ratio = short_vol / (long_vol + 1e-9)
        price_pos = (data['close'] - data['low'].rolling(20).min()) / (data['high'].rolling(20).max() - data['low'].rolling(20).min() + 1e-9)
        result = -1 * (vol_ratio * abs(price_pos - 0.5) * 2).clip(-1, 1)
        return result
