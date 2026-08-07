"""AI因子: 波动率偏度反转 | 置信:55% | 通过比较上行波动与下行波动的不对称性，识别市场尾部风险。当下行波动显著大于上行波动时，因子为负表明空头风险累积；反之因子为正提示空头挤压可能。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilitySkewReversal(BaseFactor):
    """通过比较上行波动与下行波动的不对称性，识别市场尾部风险。当下行波动显著大于上行波动时，因子为负表明空头风险累积；反之因子为正提示空头挤压可能。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_skew",
            name="Volatility Skew Reversal",
            display_name="波动率偏度反转",
            description="通过比较上行波动与下行波动的不对称性，识别市场尾部风险。当下行波动显著大于上行波动时，因子为负表明空头风险累积；反之因子为正提示空头挤压可能。",
            category="derivatives",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        df = data.copy()
        # 计算日收益率
        ret = df['close'].pct_change().fillna(0)
        # 分别计算上行和下行波动率（过去20日）
        up_ret = ret.where(ret > 0, 0)
        down_ret = ret.where(ret < 0, 0)
        up_vol = up_ret.rolling(20).std()
        down_vol = down_ret.rolling(20).std()
        # 偏度指标: (down_vol - up_vol) / (up_vol + down_vol + 1e-8)
        skew = (down_vol - up_vol) / (up_vol + down_vol + 1e-8)
        # 加上近期反转修正: 如果最近3日累计收益为正且偏度为正，可能挤压
        ret_3 = df['close'].pct_change(3).fillna(0)
        raw = skew * (1 - np.sign(ret_3) * 0.3)  # 如果上涨则下调偏度影响
        result = np.tanh(raw * 3)
        return result
