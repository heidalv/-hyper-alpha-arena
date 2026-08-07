"""AI因子: 趋势衰减风险 | 置信:60% | 结合趋势强度和价格位置，当趋势强度（ADX）较低且价格接近近期区间边界时，因子为负，表明趋势可能衰竭，适合反向操作或避免追涨杀跌。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendFadeRisk(BaseFactor):
    """结合趋势强度和价格位置，当趋势强度（ADX）较低且价格接近近期区间边界时，因子为负，表明趋势可能衰竭，适合反向操作或避免追涨杀跌。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_fade",
            name="Trend Fade Risk",
            display_name="趋势衰减风险",
            description="结合趋势强度和价格位置，当趋势强度（ADX）较低且价格接近近期区间边界时，因子为负，表明趋势可能衰竭，适合反向操作或避免追涨杀跌。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        # ADX计算
        up = high.diff()
        down = -low.diff()
        up[up < 0] = 0
        down[down < 0] = 0
        sma_up = up.rolling(14).mean()
        sma_down = down.rolling(14).mean()
        dx = 100 * np.abs(sma_up - sma_down) / (sma_up + sma_down + 1e-10)
        adx = dx.rolling(14).mean()
        # 归一化ADX
        adx_norm = adx / 100
        # 价格在近期区间内的位置 (0~1)
        past_high = high.rolling(20).max()
        past_low = low.rolling(20).min()
        pos = (close - past_low) / (past_high - past_low + 1e-10)
        # 区间边界因子：接近1或0时表示过度拉伸
        edge = np.abs(pos - 0.5) * 2  # 0~1，越接近1越极端
        # 组合：低ADX + 极端位置 => 趋势衰竭风险
        risk = (1 - adx_norm) * edge
        # 映射到[-1,1]，正值表示风险高，取负值作为因子（因为我们要避免这种状态）
        result = -risk * 2 + 1  # 使[-1,1]范围，负值表示高风险
        return result
