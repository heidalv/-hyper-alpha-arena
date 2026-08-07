"""AI因子: 价格稳定性因子 | 置信:60% | 计算过去N根K线内价格变动幅度与平均绝对变动的比率，用于度量价格的稳定性。值接近1表示价格稳定，接近-1表示价格剧烈波动。旨在规避微小波动导致的亏损（如master_running_close_tiny）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Price Stability Factor(BaseFactor):
    """计算过去N根K线内价格变动幅度与平均绝对变动的比率，用于度量价格的稳定性。值接近1表示价格稳定，接近-1表示价格剧烈波动。旨在规避微小波动导致的亏损（如master_running_close_tiny）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_stability",
            name="Price Stability Factor",
            display_name="价格稳定性因子",
            description="计算过去N根K线内价格变动幅度与平均绝对变动的比率，用于度量价格的稳定性。值接近1表示价格稳定，接近-1表示价格剧烈波动。旨在规避微小波动导致的亏损（如master_running_close_tiny）。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            N = 20
            close = data['close']
            # 计算对数收益率
            returns = np.log(close / close.shift(1))
            # 滚动窗口内的波动标准差
            vol = returns.rolling(N).std()
            # 滚动窗口内的平均绝对收益率
            mad = returns.abs().rolling(N).mean()
            # 稳定性指标：1 - vol/(mad*1.5+1e-10)，防止除零
            stability = 1 - vol / (mad * 1.5 + 1e-10)
            # 截断到[-1,1]
            result = np.clip(stability, -1, 1)
            return result
