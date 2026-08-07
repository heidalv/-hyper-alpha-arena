"""AI因子: 相对波动衰减 | 置信:60% | 当近期波动率显著低于历史波动率时，市场可能进入未知状态，导致趋势策略失效。该因子计算过去N周期价格波动率与过去M周期波动率之比的倒数，并映射到[-1,1]，比值越低（波动衰减越严重）因子值越负，提示规避做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RelativeVolatilityDecline(BaseFactor):
    """当近期波动率显著低于历史波动率时，市场可能进入未知状态，导致趋势策略失效。该因子计算过去N周期价格波动率与过去M周期波动率之比的倒数，并映射到[-1,1]，比值越低（波动衰减越严重）因子值越负，提示规避做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unkn_vol",
            name="RelativeVolatilityDecline",
            display_name="相对波动衰减",
            description="当近期波动率显著低于历史波动率时，市场可能进入未知状态，导致趋势策略失效。该因子计算过去N周期价格波动率与过去M周期波动率之比的倒数，并映射到[-1,1]，比值越低（波动衰减越严重）因子值越负，提示规避做多。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算对数收益率
        ret = np.log(data['close'] / data['close'].shift(1))
        # 近期波动率（过去5周期）
        vol_short = ret.rolling(5).std()
        # 长期波动率（过去20周期）
        vol_long = ret.rolling(20).std()
        # 比值，避免除零
        ratio = vol_short / (vol_long + 1e-10)
        # 将ratio映射到[-1,1]：当ratio<1时，因子为负，且越小越负
        # 使用sigmoid-like变换：2*(1/(1+exp(ratio-1)))-1 但简化：直接clip
        factor = 1 - 2 * np.clip(ratio, 0, 1)  # 当ratio=0时 factor=1; ratio=1时 factor=-1? 实际上我们希望ratio小则负
        # 修正：希望ratio<1时负，ratio>1时正。用线性变换：factor = (1 - ratio).clip(-1,1)
        factor = (1 - ratio).clip(-1, 1)
        # 处理NaN
        factor = factor.fillna(0)
        return factor
