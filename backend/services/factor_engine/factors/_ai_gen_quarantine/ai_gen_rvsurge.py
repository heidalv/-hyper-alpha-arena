"""AI因子: 波动率突变 | 置信:65% | 识别短期波动率的异常激增，反映市场进入不确定状态，类似亏损中'regime=unknown'的风险。计算最近10根K线的收益率标准差与过去50根K线滚动标准差的比值，当比值超过1.5时发出负向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilitySurge(BaseFactor):
    """识别短期波动率的异常激增，反映市场进入不确定状态，类似亏损中'regime=unknown'的风险。计算最近10根K线的收益率标准差与过去50根K线滚动标准差的比值，当比值超过1.5时发出负向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rvsurge",
            name="Volatility Surge",
            display_name="波动率突变",
            description="识别短期波动率的异常激增，反映市场进入不确定状态，类似亏损中'regime=unknown'的风险。计算最近10根K线的收益率标准差与过去50根K线滚动标准差的比值，当比值超过1.5时发出负向信号。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change()
        short_vol = ret.rolling(10).std()
        long_vol = ret.rolling(50).std()
        ratio = short_vol / long_vol
        ratio = ratio.fillna(1.0)
        factor = -1.0 * (ratio - 1.5) / 0.5
        factor = factor.clip(-1.0, 1.0)
        return factor
