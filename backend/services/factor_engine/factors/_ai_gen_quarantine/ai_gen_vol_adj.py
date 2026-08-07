"""AI因子: 波动率调整动量因子 | 置信:65% | 计算近期价格变化率除以ATR（平均真实波幅），衡量单位风险下的动量。高值表示强趋势且波动可控，适合做多；低值或负值表示方向不明或反转概率高，易触发止损/止盈。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentum(BaseFactor):
    """计算近期价格变化率除以ATR（平均真实波幅），衡量单位风险下的动量。高值表示强趋势且波动可控，适合做多；低值或负值表示方向不明或反转概率高，易触发止损/止盈。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_adj",
            name="VolatilityAdjustedMomentum",
            display_name="波动率调整动量因子",
            description="计算近期价格变化率除以ATR（平均真实波幅），衡量单位风险下的动量。高值表示强趋势且波动可控，适合做多；低值或负值表示方向不明或反转概率高，易触发止损/止盈。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        # 计算ATR(14)
        prev_close = close.shift(1)
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 价格变化率（10周期）
        ret = close.pct_change(10)
        # 用ATR归一化
        norm_ret = ret / (atr / close)  # 单位：价格变化/ATR比率
        # clip到[-3,3]然后用tanh映射到[-1,1]
        import numpy as np
        result = np.tanh(norm_ret.fillna(0) * 0.5)
        return result
