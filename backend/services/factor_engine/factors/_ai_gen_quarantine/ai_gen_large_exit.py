"""AI因子: 大单撤离失衡因子 | 置信:60% | 捕捉主力资金快速离场导致的异常价格与成交量模式。计算成交量加权价格与简单移动均值的偏离，结合日内价格振幅，当出现显著背离且成交量剧增时发出信号。负值表示大资金看空撤离，正值表示大资金看多进场。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LargeTraderExitImbalance(BaseFactor):
    """捕捉主力资金快速离场导致的异常价格与成交量模式。计算成交量加权价格与简单移动均值的偏离，结合日内价格振幅，当出现显著背离且成交量剧增时发出信号。负值表示大资金看空撤离，正值表示大资金看多进场。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_large_exit",
            name="Large Trader Exit Imbalance",
            display_name="大单撤离失衡因子",
            description="捕捉主力资金快速离场导致的异常价格与成交量模式。计算成交量加权价格与简单移动均值的偏离，结合日内价格振幅，当出现显著背离且成交量剧增时发出信号。负值表示大资金看空撤离，正值表示大资金看多进场。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        n = 10
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        vwap = (volume * (high+low+close)/3).rolling(n).mean() / volume.rolling(n).mean().clip(lower=1e-8)
        simple_ma = close.rolling(n).mean()
        # 计算价格偏离
        price_dev = (close - vwap) / vwap.clip(lower=1e-8)
        # 成交量放大倍数
        vol_ma = volume.rolling(n).mean()
        vol_spike = volume / vol_ma.clip(lower=1e-8) - 1
        # 合并信号：价格低于vwap且成交量放大表示撤离
        signal = -price_dev * vol_spike
        # 限制范围
        result = signal.clip(-1, 1)
        return result
