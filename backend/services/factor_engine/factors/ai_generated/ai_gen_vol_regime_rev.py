"""AI因子: 波动率切换反转 | 置信:57% | 以短长周期已实现波动率之比刻画波动率突变，当短期波动显著高于长期波动（比值高）时，短期收益更易反转，故用负号乘以短期收益；低波动环境下动量延续。构造波动率状态自适应的反转/动量因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityRegimeReversal(BaseFactor):
    """以短长周期已实现波动率之比刻画波动率突变，当短期波动显著高于长期波动（比值高）时，短期收益更易反转，故用负号乘以短期收益；低波动环境下动量延续。构造波动率状态自适应的反转/动量因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_regime_rev",
            name="Volatility Regime Reversal",
            display_name="波动率切换反转",
            description="以短长周期已实现波动率之比刻画波动率突变，当短期波动显著高于长期波动（比值高）时，短期收益更易反转，故用负号乘以短期收益；低波动环境下动量延续。构造波动率状态自适应的反转/动量因子。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        r1 = data['close'].pct_change()
        vol_s = r1.rolling(5).std()
        vol_l = r1.rolling(30).std() + 1e-9
        ratio = (vol_s / vol_l).clip(0, 5)
        ret5 = data['close'].pct_change(5)
        result = (-ret5 * (ratio - 1)).clip(-1, 1)
        return result
