"""AI因子: 趋势质量因子 | 置信:60% | 基于布林带宽度与价格位置，识别市场是否处于强趋势或震荡状态。当带宽较宽且价格沿带宽边缘运行时视为强趋势（利多），当带宽较窄且价格在带内随机波动时视为震荡（利空）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Quality(BaseFactor):
    """基于布林带宽度与价格位置，识别市场是否处于强趋势或震荡状态。当带宽较宽且价格沿带宽边缘运行时视为强趋势（利多），当带宽较窄且价格在带内随机波动时视为震荡（利空）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trd_qlt",
            name="Trend Quality",
            display_name="趋势质量因子",
            description="基于布林带宽度与价格位置，识别市场是否处于强趋势或震荡状态。当带宽较宽且价格沿带宽边缘运行时视为强趋势（利多），当带宽较窄且价格在带内随机波动时视为震荡（利空）。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']

        # 20日布林带
        window = 20
        sma = close.rolling(window).mean()
        std = close.rolling(window).std()
        upper = sma + 2 * std
        lower = sma - 2 * std
        band_width = (upper - lower) / sma

        # 价格在布林带内的相对位置（0~1）
        pos = (close - lower) / (upper - lower)
        # 当价格靠近边缘且带宽大时趋势强，使用带宽乘极值位置
        # 极值位置：pos接近0或1时趋势强，中间弱
        extreme = 1 - 2 * np.abs(pos - 0.5)  # 0~1，边缘为1
        raw = band_width * extreme
        # 归一化到[-1,1]：raw最大值一般小于0.5（取决于波动），用2倍截断
        normalized = np.clip(raw * 4, -1, 1)
        result = normalized.fillna(0)
        return result
