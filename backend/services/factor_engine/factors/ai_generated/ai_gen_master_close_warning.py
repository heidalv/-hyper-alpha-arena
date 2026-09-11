"""AI因子: 主程序强平预警 | 置信:60% | master_running_close导致的亏损集中在BNB和VIRTUAL，且亏损比例较大。构建一个基于价格偏离均线程度和成交量异动的因子，当价格快速偏离且量能异常时发出负向信号，模拟主程序强平前的市场特征。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MasterCloseWarning(BaseFactor):
    """master_running_close导致的亏损集中在BNB和VIRTUAL，且亏损比例较大。构建一个基于价格偏离均线程度和成交量异动的因子，当价格快速偏离且量能异常时发出负向信号，模拟主程序强平前的市场特征。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_master_close_warning",
            name="Master Close Warning",
            display_name="主程序强平预警",
            description="master_running_close导致的亏损集中在BNB和VIRTUAL，且亏损比例较大。构建一个基于价格偏离均线程度和成交量异动的因子，当价格快速偏离且量能异常时发出负向信号，模拟主程序强平前的市场特征。",
            category="behavioral",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ma20 = data['close'].rolling(20).mean()
        dev = (data['close'] - ma20) / (ma20 + 1e-9)
        vol_ratio = data['volume'] / (data['volume'].rolling(20).median() + 1e-9)
        price_speed = data['close'].pct_change(3).abs()
        result = (-dev * vol_ratio * price_speed).clip(-1, 1)
        return result
