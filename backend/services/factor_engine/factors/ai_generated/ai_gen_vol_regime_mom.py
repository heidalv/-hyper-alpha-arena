"""AI因子: 波动率状态动量 | 置信:50% | 用短期已实现波动率与长期波动率之比识别波动状态切换：低波动向高波动切换时动量延续性强，高波动收缩时动量易反转。将波动率比率与中期收益方向交互，捕捉波动聚集下的趋势 alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityRegimeMomentum(BaseFactor):
    """用短期已实现波动率与长期波动率之比识别波动状态切换：低波动向高波动切换时动量延续性强，高波动收缩时动量易反转。将波动率比率与中期收益方向交互，捕捉波动聚集下的趋势 alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_regime_mom",
            name="Volatility Regime Momentum",
            display_name="波动率状态动量",
            description="用短期已实现波动率与长期波动率之比识别波动状态切换：低波动向高波动切换时动量延续性强，高波动收缩时动量易反转。将波动率比率与中期收益方向交互，捕捉波动聚集下的趋势 alpha。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        r = data['close'].pct_change()
        vol_s = r.rolling(5).std()
        vol_l = r.rolling(30).std() + 1e-9
        ratio = vol_s / vol_l
        mom = data['close'].pct_change(10)
        result = (mom * 20.0 * (ratio - 1.0)).clip(-1, 1)
        return result
