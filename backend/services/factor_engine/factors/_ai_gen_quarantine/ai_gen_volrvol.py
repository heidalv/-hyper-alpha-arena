"""AI因子: 成交量调整相对波动率 | 置信:60% | 计算短期（5周期）波动率与长期（20周期）波动率的比值，并用成交量变化进行调节。当短期波动率相对长期急剧放大且伴随成交量萎缩时，做多风险增加，因子趋于负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_adjusted_Relative_Volatility(BaseFactor):
    """计算短期（5周期）波动率与长期（20周期）波动率的比值，并用成交量变化进行调节。当短期波动率相对长期急剧放大且伴随成交量萎缩时，做多风险增加，因子趋于负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volrvol",
            name="Volume-adjusted Relative Volatility",
            display_name="成交量调整相对波动率",
            description="计算短期（5周期）波动率与长期（20周期）波动率的比值，并用成交量变化进行调节。当短期波动率相对长期急剧放大且伴随成交量萎缩时，做多风险增加，因子趋于负值。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        ret = close.pct_change()
        short_vol = ret.rolling(5).std()
        long_vol = ret.rolling(20).std()
        vol_ratio = short_vol / long_vol
        vol_change = volume / volume.rolling(5).mean()
        # 当波动率比值高但成交量下降时，因子为负
        factor = -(vol_ratio * (1 - vol_change)).fillna(0)
        return factor.clip(-1, 1)
