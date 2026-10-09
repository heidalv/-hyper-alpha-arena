"""AI因子: 做空挤压防护 | 置信:55% | 亏损中short在unknown regime下多次触发止损，可能因短期反弹导致。当价格从低位快速回升（短期涨幅大）且成交量放大时，做空风险高。该因子在超卖反弹+放量时输出负值（提示避免做空），在持续下跌+缩量时输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortSqueezeGuard(BaseFactor):
    """亏损中short在unknown regime下多次触发止损，可能因短期反弹导致。当价格从低位快速回升（短期涨幅大）且成交量放大时，做空风险高。该因子在超卖反弹+放量时输出负值（提示避免做空），在持续下跌+缩量时输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_short_squeeze_guard",
            name="Short Squeeze Guard",
            display_name="做空挤压防护",
            description="亏损中short在unknown regime下多次触发止损，可能因短期反弹导致。当价格从低位快速回升（短期涨幅大）且成交量放大时，做空风险高。该因子在超卖反弹+放量时输出负值（提示避免做空），在持续下跌+缩量时输出正值。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        ret5 = close.pct_change(5)
        vol_ma = volume.rolling(20).mean()
        vol_ratio = volume / (vol_ma + 1e-9)
        lower = close.rolling(20).min()
        upper = close.rolling(20).max()
        pos = (close - lower) / (upper - lower + 1e-9)
        result = (ret5 * vol_ratio - pos).clip(-1, 1)
        return result
