"""AI因子: 量价背离风险 | 置信:55% | 计算价格变化与成交量变化的相关性，当价格在狭窄区间内但成交量放大时，表明多空分歧大、方向不明，容易引发非预期平仓，返回负值；价量协调为正。基于20日滚动相关系数，经tanh映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """计算价格变化与成交量变化的相关性，当价格在狭窄区间内但成交量放大时，表明多空分歧大、方向不明，容易引发非预期平仓，返回负值；价量协调为正。基于20日滚动相关系数，经tanh映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vp_risk",
            name="Volume-Price Divergence",
            display_name="量价背离风险",
            description="计算价格变化与成交量变化的相关性，当价格在狭窄区间内但成交量放大时，表明多空分歧大、方向不明，容易引发非预期平仓，返回负值；价量协调为正。基于20日滚动相关系数，经tanh映射到[-1,1]。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        volume = data['volume']
        ret = close.pct_change()
        vol_change = volume.pct_change()
        # 20日滚动相关系数
        corr = ret.rolling(20).corr(vol_change)
        # 再乘以价格波动率调节：窄幅时风险更大
        atr = (data['high'] - data['low']).rolling(14).mean()
        atr_norm = atr / close
        # 窄幅权重：当ATR/close低于历史20%分位时加重负向
        threshold = atr_norm.rolling(20).quantile(0.2)
        narrow_penalty = np.where(atr_norm < threshold, -0.5, 0.0)
        result = np.tanh(2 * corr) + narrow_penalty
        result = np.clip(result, -1, 1)
        return pd.Series(result, index=data.index)
