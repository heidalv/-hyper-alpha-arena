"""AI因子: 假突破强度 | 置信:55% | 检测价格向上突破近期高点后立即反转回落的假突破模式。计算当日最高价突破过去N日高点后，收盘价相对当日高点的回落比例，若回落比例大则视为假突破，做多风险高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class False_Breakout_Intensity(BaseFactor):
    """检测价格向上突破近期高点后立即反转回落的假突破模式。计算当日最高价突破过去N日高点后，收盘价相对当日高点的回落比例，若回落比例大则视为假突破，做多风险高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_falsebreak",
            name="False Breakout Intensity",
            display_name="假突破强度",
            description="检测价格向上突破近期高点后立即反转回落的假突破模式。计算当日最高价突破过去N日高点后，收盘价相对当日高点的回落比例，若回落比例大则视为假突破，做多风险高。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        # 过去20日最高点
        recent_high = high.rolling(20).max()
        # 是否突破：当日最高价超过最近高点
        breakout = high > recent_high.shift(1)
        # 回落幅度：从日最高到收盘的百分比
        retrace = (high - close) / (high - low + 1e-8)
        # 仅当突破时有效
        strength = breakout.astype(float) * retrace
        # 平滑处理，滚动5日均值
        result = strength.rolling(5).mean().fillna(0)
        # 映射到[-1,1]，数值越大说明回落越严重，越不适合做多
        result = np.clip(result * 2 - 1, -1, 1)
        return result
