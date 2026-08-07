"""AI因子: 量价背离因子 | 置信:50% | 当成交量异常放大但价格没有相应突破（即价格变化很小）时，往往意味着多空分歧严重，未来方向不明，容易导致亏损。计算过去5日成交量的相对变化率（当前量/过去5日均量-1）和价格变化率（当前收盘/5日前收盘-1），若成交量变化率高于1.5倍标准差且价格变化率绝对值低于0.5倍标准差，则返回-1，否则根据偏离程度线性映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Price_Divergence_Factor(BaseFactor):
    """当成交量异常放大但价格没有相应突破（即价格变化很小）时，往往意味着多空分歧严重，未来方向不明，容易导致亏损。计算过去5日成交量的相对变化率（当前量/过去5日均量-1）和价格变化率（当前收盘/5日前收盘-1），若成交量变化率高于1.5倍标准差且价格变化率绝对值低于0.5倍标准差，则返回-1，否则根据偏离程度线性映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volum",
            name="Volume-Price Divergence Factor",
            display_name="量价背离因子",
            description="当成交量异常放大但价格没有相应突破（即价格变化很小）时，往往意味着多空分歧严重，未来方向不明，容易导致亏损。计算过去5日成交量的相对变化率（当前量/过去5日均量-1）和价格变化率（当前收盘/5日前收盘-1），若成交量变化率高于1.5倍标准差且价格变化率绝对值低于0.5倍标准差，则返回-1，否则根据偏离程度线性映射到[-1,1]。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        volume = data['volume']
        vol_ma5 = volume.rolling(5).mean()
        vol_ratio = volume / vol_ma5 - 1.0
        price_ret = close.diff(5) / close.shift(5)
        vol_std = vol_ratio.rolling(20).std()
        price_std = price_ret.rolling(20).std()
        # Condition: vol_ratio > 1.5*vol_std and |price_ret| < 0.5*price_std
        cond = (vol_ratio > 1.5 * vol_std) & (np.abs(price_ret) < 0.5 * price_std)
        result = pd.Series(np.where(cond, -1.0, 0.0), index=close.index)
        # Smooth and map to [-1,1] with intensity
        # Scale by how extreme the vol ratio is
        intensity = vol_ratio / (3 * vol_std)  # cap at 1
        result = result * np.clip(intensity, 0, 1)
        return result.fillna(0.0)
