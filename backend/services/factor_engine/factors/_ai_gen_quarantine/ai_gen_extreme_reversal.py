"""AI因子: 极端乖离反转因子 | 置信:60% | 基于价格与长期均线的乖离率，结合成交量确认。当价格偏离60日均线超过2个标准差且成交量放量时，预示均值回归。计算Z-score绝对值超过阈值时输出反向信号，并用量化成交量确认强度。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ExtremeDeviationReversal(BaseFactor):
    """基于价格与长期均线的乖离率，结合成交量确认。当价格偏离60日均线超过2个标准差且成交量放量时，预示均值回归。计算Z-score绝对值超过阈值时输出反向信号，并用量化成交量确认强度。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_extreme_reversal",
            name="Extreme Deviation Reversal",
            display_name="极端乖离反转因子",
            description="基于价格与长期均线的乖离率，结合成交量确认。当价格偏离60日均线超过2个标准差且成交量放量时，预示均值回归。计算Z-score绝对值超过阈值时输出反向信号，并用量化成交量确认强度。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        volume = data['volume']
        ma60 = close.rolling(60).mean()
        std60 = close.rolling(60).std()
        # 避免除零
        z_score = (close - ma60) / (std60 + 1e-10)
        # 成交量相对均值放大程度
        vol_ma20 = volume.rolling(20).mean()
        vol_ratio = volume / (vol_ma20 + 1e-10)
        # 信号：当|z_score|>2且vol_ratio>1.5时，反向交易
        signal = np.where(np.abs(z_score) > 2, -np.sign(z_score) * 1.0, 0.0)
        # 用成交量强度缩放
        vol_scale = np.clip(vol_ratio / 2, 0, 1)  # 0~1
        result = signal * vol_scale
        # 软clip
        result = np.clip(result, -1, 1)
        return result
