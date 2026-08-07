"""AI因子: 趋势效率因子 | 置信:65% | 基于效率系数(Efficiency Ratio)度量市场趋势强度与方向。效率系数=净价格变化/总价格变化绝对值之和，值域[0,1]。乘以方向后映射到[-1,1]，当市场无明显趋势（噪音高）时接近0。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendEfficiencyFactor(BaseFactor):
    """基于效率系数(Efficiency Ratio)度量市场趋势强度与方向。效率系数=净价格变化/总价格变化绝对值之和，值域[0,1]。乘以方向后映射到[-1,1]，当市场无明显趋势（噪音高）时接近0。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_efficiency",
            name="Trend Efficiency Factor",
            display_name="趋势效率因子",
            description="基于效率系数(Efficiency Ratio)度量市场趋势强度与方向。效率系数=净价格变化/总价格变化绝对值之和，值域[0,1]。乘以方向后映射到[-1,1]，当市场无明显趋势（噪音高）时接近0。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        n = 14
        # 效率系数
        change = close.diff(n)
        total_movement = close.diff().abs().rolling(n).sum()
        eff_ratio = change.abs() / total_movement
        eff_ratio = eff_ratio.fillna(0)
        direction = np.sign(change)
        # 映射到[-1,1]，当eff_ratio=0.5时输出0
        factor = direction * (2 * eff_ratio - 1)
        factor = factor.clip(-1, 1)
        return factor
