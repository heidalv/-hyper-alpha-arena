"""AI因子: 量价背离 | 置信:55% | 价格波动率与成交量变化率的背离程度。当价格大幅波动但成交量萎缩时，说明流动性不足或虚假突破，容易触发止损。正值表示高风险虚假波动，负值表示健康趋势。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeVolatilityDivergence(BaseFactor):
    """价格波动率与成交量变化率的背离程度。当价格大幅波动但成交量萎缩时，说明流动性不足或虚假突破，容易触发止损。正值表示高风险虚假波动，负值表示健康趋势。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volumevol",
            name="Volume Volatility Divergence",
            display_name="量价背离",
            description="价格波动率与成交量变化率的背离程度。当价格大幅波动但成交量萎缩时，说明流动性不足或虚假突破，容易触发止损。正值表示高风险虚假波动，负值表示健康趋势。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 价格波动率：20周期标准差
        price_vol = close.rolling(20).std() / close.rolling(20).mean() * 100
        # 成交量变化率：20周期相对标准差
        volume_vol = volume.rolling(20).std() / volume.rolling(20).mean() * 100
        # 背离：价格波动高而成交量波动低
        diff = price_vol - volume_vol
        # 标准化
        result = (diff - diff.rolling(100).mean()) / (diff.rolling(100).std() + 1e-10)
        result = np.clip(result, -3, 3) / 3.0
        return result
