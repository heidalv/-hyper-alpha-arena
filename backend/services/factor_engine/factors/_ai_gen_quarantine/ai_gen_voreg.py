"""AI因子: 波动率状态突变 | 置信:70% | 检测波动率是否发生显著突变，当近期波动率与中期波动率比值异常高或低时，表明市场进入未知状态，容易导致趋势策略失效。输出-1到0，值越负风险越高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityRegimeShift(BaseFactor):
    """检测波动率是否发生显著突变，当近期波动率与中期波动率比值异常高或低时，表明市场进入未知状态，容易导致趋势策略失效。输出-1到0，值越负风险越高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_voreg",
            name="VolatilityRegimeShift",
            display_name="波动率状态突变",
            description="检测波动率是否发生显著突变，当近期波动率与中期波动率比值异常高或低时，表明市场进入未知状态，容易导致趋势策略失效。输出-1到0，值越负风险越高。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ret = close.pct_change().dropna()
        short_vol = ret.rolling(10).std()
        long_vol = ret.rolling(50).std()
        ratio = short_vol / long_vol
        # 将比值映射到[-1,0]，以2.0和0.5为阈值
        result = -1 * ((ratio > 2.0) | (ratio < 0.5)).astype(float)
        result = result.fillna(0)
        return result
