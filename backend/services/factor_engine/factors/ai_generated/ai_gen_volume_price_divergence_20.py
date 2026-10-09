"""AI因子: 量价背离 | 置信:58% | 量价背离因子：价格短期涨幅与成交量变化的差异。价格上行但量能萎缩(缩量上涨)预示动能不足易回落；价格下行但放量(恐慌抛售)预示超卖反弹。用价格收益与量能变化的差除以波动率归一化。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """量价背离因子：价格短期涨幅与成交量变化的差异。价格上行但量能萎缩(缩量上涨)预示动能不足易回落；价格下行但放量(恐慌抛售)预示超卖反弹。用价格收益与量能变化的差除以波动率归一化。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_price_divergence_20",
            name="Volume Price Divergence",
            display_name="量价背离",
            description="量价背离因子：价格短期涨幅与成交量变化的差异。价格上行但量能萎缩(缩量上涨)预示动能不足易回落；价格下行但放量(恐慌抛售)预示超卖反弹。用价格收益与量能变化的差除以波动率归一化。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(10)
        vol_chg = data['volume'].pct_change(10)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((ret - vol_chg * 0.5) / vol).clip(-1, 1)
        return result
