"""AI因子: 均值回复风险 | 置信:55% | 基于布林带指标，判断价格是否处于中轨附近且带宽较宽，此时市场缺乏明确方向，容易受假突破影响而导致做多亏损。因子值接近-1表示价格在通道中部且带宽宽（高风险），接近+1表示价格在通道边缘或带宽窄（方向明确）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Mean_Reversion_Risk(BaseFactor):
    """基于布林带指标，判断价格是否处于中轨附近且带宽较宽，此时市场缺乏明确方向，容易受假突破影响而导致做多亏损。因子值接近-1表示价格在通道中部且带宽宽（高风险），接近+1表示价格在通道边缘或带宽窄（方向明确）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_revert",
            name="Mean Reversion Risk",
            display_name="均值回复风险",
            description="基于布林带指标，判断价格是否处于中轨附近且带宽较宽，此时市场缺乏明确方向，容易受假突破影响而导致做多亏损。因子值接近-1表示价格在通道中部且带宽宽（高风险），接近+1表示价格在通道边缘或带宽窄（方向明确）。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        window = 20
        sma = close.rolling(window).mean()
        std = close.rolling(window).std(ddof=0)
        upper = sma + 2 * std
        lower = sma - 2 * std
        # 价格在通道中的相对位置 [-1,1]
        pos = 2 * (close - sma) / (upper - lower + 1e-10)
        # 带宽（标准差/均价）的滚动百分位
        bandwidth = std / (sma + 1e-10)
        # 滚动百分位（过去100天）
        rank = bandwidth.rolling(100).apply(lambda x: (x.iloc[-1] < x).mean(), raw=False)
        # 因子：位置越接近0且带宽越大 -> 越接近-1
        factor = - (1 - abs(pos)) * rank
        factor = factor.fillna(0).clip(-1, 1)
        return factor
