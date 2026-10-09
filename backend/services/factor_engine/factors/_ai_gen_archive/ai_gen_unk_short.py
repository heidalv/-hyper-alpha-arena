"""AI因子: 未知状态做空偏置 | 置信:60% | 亏损集中在regime=unknown且做空方向，利用价格与均线偏离度及波动收缩识别弱势反弹，做空动量衰减。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class UnknownRegimeShortBias(BaseFactor):
    """亏损集中在regime=unknown且做空方向，利用价格与均线偏离度及波动收缩识别弱势反弹，做空动量衰减。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unk_short",
            name="Unknown_Regime_Short_Bias",
            display_name="未知状态做空偏置",
            description="亏损集中在regime=unknown且做空方向，利用价格与均线偏离度及波动收缩识别弱势反弹，做空动量衰减。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        ma = data['close'].rolling(20).mean()
        dev = (data['close'] - ma) / (ma + 1e-9)
        vol = data['close'].pct_change().rolling(10).std()
        result = (-ret * dev / (vol + 1e-9)).clip(-1, 1)
        return result
