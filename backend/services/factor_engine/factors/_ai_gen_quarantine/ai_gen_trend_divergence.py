"""AI因子: 趋势背离信号 | 置信:60% | 衡量短期均线与长期均线的方向背离程度。当短长期趋势不一致（一个向上一个向下）时，市场处于不确定状态，因子值接近+1；当方向一致时接近-1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendDivergenceSignal(BaseFactor):
    """衡量短期均线与长期均线的方向背离程度。当短长期趋势不一致（一个向上一个向下）时，市场处于不确定状态，因子值接近+1；当方向一致时接近-1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_divergence",
            name="Trend Divergence Signal",
            display_name="趋势背离信号",
            description="衡量短期均线与长期均线的方向背离程度。当短长期趋势不一致（一个向上一个向下）时，市场处于不确定状态，因子值接近+1；当方向一致时接近-1。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        # 短期均线（5周期）和长期均线（30周期）的斜率（使用线性回归或简单差分）
        short_ma = close.rolling(window=5).mean()
        long_ma = close.rolling(window=30).mean()
        # 用差分近似斜率
        short_slope = short_ma.diff(1)  # 1周期变化
        long_slope = long_ma.diff(1)
        # 计算方向一致性：如果符号相同则-1，相反则+1
        sign_product = np.sign(short_slope) * np.sign(long_slope)
        # 处理NaN和0，0视为不确定
        result = -sign_product  # 相同方向->-1，相反方向->+1
        result = result.fillna(0.0)
        # 限制范围
        result = np.clip(result, -1, 1)
        return result
