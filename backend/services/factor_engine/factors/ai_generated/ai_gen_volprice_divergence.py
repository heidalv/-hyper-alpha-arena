"""AI因子: 量价背离反转 | 置信:58% | 价格短期涨幅与成交量变化的背离度。价格上行但成交量萎缩(量价背离)预示动能不足，未来回落概率高；价格下跌但放量(恐慌抛售)预示超卖反弹。用成交量z-score与收益方向交互构造反转因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergenceReversal(BaseFactor):
    """价格短期涨幅与成交量变化的背离度。价格上行但成交量萎缩(量价背离)预示动能不足，未来回落概率高；价格下跌但放量(恐慌抛售)预示超卖反弹。用成交量z-score与收益方向交互构造反转因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volprice_divergence",
            name="Volume-Price Divergence Reversal",
            display_name="量价背离反转",
            description="价格短期涨幅与成交量变化的背离度。价格上行但成交量萎缩(量价背离)预示动能不足，未来回落概率高；价格下跌但放量(恐慌抛售)预示超卖反弹。用成交量z-score与收益方向交互构造反转因子。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret5 = data['close'].pct_change(5)
        vol_ma = data['volume'].rolling(20).mean()
        vol_std = data['volume'].rolling(20).std()
        vol_z = (data['volume'] - vol_ma) / (vol_std + 1e-9)
        result = (-ret5 * vol_z.rolling(3).mean()).clip(-1, 1)
        return result
