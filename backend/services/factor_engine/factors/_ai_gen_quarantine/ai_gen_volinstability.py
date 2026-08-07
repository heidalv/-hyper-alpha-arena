"""AI因子: 波动率状态不确定性 | 置信:70% | 计算短期与长期波动率之比，高比值表示波动率突变，易导致regime=unknown状态，进而引发各类亏损。取比率对数后归一化至[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Regime_Uncertainty(BaseFactor):
    """计算短期与长期波动率之比，高比值表示波动率突变，易导致regime=unknown状态，进而引发各类亏损。取比率对数后归一化至[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volinstability",
            name="Volatility_Regime_Uncertainty",
            display_name="波动率状态不确定性",
            description="计算短期与长期波动率之比，高比值表示波动率突变，易导致regime=unknown状态，进而引发各类亏损。取比率对数后归一化至[-1,1]。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        short_window = 10
        long_window = 60
        ret = data['close'].pct_change().fillna(0)
        short_vol = ret.rolling(window=short_window, min_periods=1).std() * np.sqrt(24*60)  # 假设分钟数据，但比例不变
        long_vol = ret.rolling(window=long_window, min_periods=1).std() * np.sqrt(24*60)
        ratio = short_vol / (long_vol + 1e-8)
        log_ratio = np.log(ratio + 1e-8)
        # 映射，典型值在-2到2之间
        return np.clip(log_ratio / 2, -1, 1)
