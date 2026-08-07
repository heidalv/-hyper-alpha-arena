"""AI因子: 波动率状态因子 | 置信:70% | 衡量近期波动率相对于历史中位数的变化程度，识别市场是否处于异常波动状态。高频亏损多发生在regime=unknown，通常对应波动率突变。因子值接近-1表示高波动异常(风险高)，接近+1表示低波动稳定(适合趋势)。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityRegimeChange(BaseFactor):
    """衡量近期波动率相对于历史中位数的变化程度，识别市场是否处于异常波动状态。高频亏损多发生在regime=unknown，通常对应波动率突变。因子值接近-1表示高波动异常(风险高)，接近+1表示低波动稳定(适合趋势)。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volregime",
            name="Volatility Regime Change",
            display_name="波动率状态因子",
            description="衡量近期波动率相对于历史中位数的变化程度，识别市场是否处于异常波动状态。高频亏损多发生在regime=unknown，通常对应波动率突变。因子值接近-1表示高波动异常(风险高)，接近+1表示低波动稳定(适合趋势)。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        returns = close.pct_change()
        vol_short = returns.rolling(10).std()
        vol_long = returns.rolling(50).std()
        # 避免除零
        vol_long = vol_long.replace(0, np.nan)
        ratio = vol_short / vol_long
        # 将ratio映射到[-1,1]：小于1为低波动，大于1为高波动
        # 使用tanh压缩，以1为中性点
        ratio_centered = ratio - 1.0
        result = np.tanh(ratio_centered * 3)  # 放大差异
        return result.fillna(0).clip(-1, 1)
