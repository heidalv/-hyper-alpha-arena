"""AI因子: 噪声波动率比 | 置信:70% | 衡量价格在短期内的波动是否主要由随机噪声驱动。计算近期(比如5分钟)的平均绝对收益率与中期(比如60分钟)的波动率之比，比值高表示噪声大，回归-1；比值低表示趋势清晰，回归+1。平滑处理以避免极端值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class NoiseVolatilityRatio(BaseFactor):
    """衡量价格在短期内的波动是否主要由随机噪声驱动。计算近期(比如5分钟)的平均绝对收益率与中期(比如60分钟)的波动率之比，比值高表示噪声大，回归-1；比值低表示趋势清晰，回归+1。平滑处理以避免极端值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_noise_vol",
            name="Noise Volatility Ratio",
            display_name="噪声波动率比",
            description="衡量价格在短期内的波动是否主要由随机噪声驱动。计算近期(比如5分钟)的平均绝对收益率与中期(比如60分钟)的波动率之比，比值高表示噪声大，回归-1；比值低表示趋势清晰，回归+1。平滑处理以避免极端值。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        ret = data['close'].pct_change()
        short_vol = ret.rolling(5).std()
        mid_vol = ret.rolling(60).std()
        ratio = short_vol / (mid_vol + 1e-10)
        result = 1 - 2 * (ratio - ratio.rolling(20).min()) / (ratio.rolling(20).max() - ratio.rolling(20).min() + 1e-10)
        return result.clip(-1, 1)
