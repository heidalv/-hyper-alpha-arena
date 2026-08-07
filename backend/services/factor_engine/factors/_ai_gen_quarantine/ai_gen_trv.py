"""AI因子: 小单频繁平仓风险 | 置信:50% | 通过计算短期价格波动与成交量比值，识别因微小波动导致的频繁平仓风险。当价格波动小而成交量异常增大时，市场可能处于不稳定状态，容易触发小单平仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TinyRunningVolatility(BaseFactor):
    """通过计算短期价格波动与成交量比值，识别因微小波动导致的频繁平仓风险。当价格波动小而成交量异常增大时，市场可能处于不稳定状态，容易触发小单平仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trv",
            name="TinyRunningVolatility",
            display_name="小单频繁平仓风险",
            description="通过计算短期价格波动与成交量比值，识别因微小波动导致的频繁平仓风险。当价格波动小而成交量异常增大时，市场可能处于不稳定状态，容易触发小单平仓。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        # 计算每笔价格变化范围（百分比）
        price_range = (high - low) / close.shift(1)
        # 计算成交量相对近期均值的异常比
        vol_ma = volume.rolling(5).mean()
        vol_ratio = volume / vol_ma
        # 小波动但成交量异常 => 风险高
        raw = -price_range * vol_ratio  # 负值表示风险
        # 滚动标准化到[-1,1]
        norm = (raw - raw.rolling(20).mean()) / (raw.rolling(20).std() + 1e-8)
        return norm.clip(-1, 1)
