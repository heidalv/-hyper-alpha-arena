"""AI因子: 噪声比率指标 | 置信:60% | 衡量近期价格运动中的噪声程度，计算平均真实波幅与绝对价格变化之比。高值表示高噪声（震荡市），低值表示趋势明确。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class NoiseRatioIndicator(BaseFactor):
    """衡量近期价格运动中的噪声程度，计算平均真实波幅与绝对价格变化之比。高值表示高噪声（震荡市），低值表示趋势明确。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_nri",
            name="NoiseRatioIndicator",
            display_name="噪声比率指标",
            description="衡量近期价格运动中的噪声程度，计算平均真实波幅与绝对价格变化之比。高值表示高噪声（震荡市），低值表示趋势明确。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        prev_close = close.shift(1)
        # 计算真实波幅
        tr1 = high - low
        tr2 = np.abs(high - prev_close)
        tr3 = np.abs(low - prev_close)
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 绝对价格变化
        price_change = np.abs(close - prev_close)
        # 避免除以0
        ratio = atr / (price_change + 1e-8)
        # 归一化到[-1,1]：假设合理范围0.5~3，中心化
        normalized = 1 - (ratio - 0.5) / 2.5  # 当ratio=0.5时值1，ratio=3时值0，再平移至[-1,1]
        result = np.clip(normalized * 2 - 1, -1, 1)
        return result
