"""AI因子: 波动率调整动量 | 置信:65% | 使用过去N日波动率对传统动量进行缩放，当波动率过低时降低动量信号强度，避免在低波动期追涨杀跌导致的止损亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentum(BaseFactor):
    """使用过去N日波动率对传统动量进行缩放，当波动率过低时降低动量信号强度，避免在低波动期追涨杀跌导致的止损亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_voladjmom",
            name="Volatility-Adjusted Momentum",
            display_name="波动率调整动量",
            description="使用过去N日波动率对传统动量进行缩放，当波动率过低时降低动量信号强度，避免在低波动期追涨杀跌导致的止损亏损。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        window = 20
        vol_window = 20
        close = data['close']
        returns = close.pct_change()
        momentum = close / close.shift(window) - 1
        # 波动率：过去vol_window日收益率的年化标准差（按日计算）
        volatility = returns.rolling(vol_window).std() * np.sqrt(252)
        # 防止除零
        vol_normalized = volatility / volatility.rolling(vol_window).mean()
        vol_normalized = vol_normalized.clip(lower=0.3, upper=3.0)  # 截断极端值
        # 信号：动量除以波动率调整因子，再映射到[-1,1]
        raw_signal = momentum / vol_normalized
        # 用tanh压缩
        result = np.tanh(raw_signal * 5)
        # 处理NaN
        result = result.fillna(0.0)
        return result.clip(-1.0, 1.0)
