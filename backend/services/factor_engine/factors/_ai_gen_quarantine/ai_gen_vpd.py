"""AI因子: 量价背离因子 | 置信:60% | 识别价格与成交量的背离现象：价格创近期新高但成交量萎缩，或价格创近期新低但成交量放大，预示反转风险。使用20日价格区间位置与成交量相对位置之差，正值表示看跌背离，负值表示看涨背离，映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """识别价格与成交量的背离现象：价格创近期新高但成交量萎缩，或价格创近期新低但成交量放大，预示反转风险。使用20日价格区间位置与成交量相对位置之差，正值表示看跌背离，负值表示看涨背离，映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vpd",
            name="Volume Price Divergence",
            display_name="量价背离因子",
            description="识别价格与成交量的背离现象：价格创近期新高但成交量萎缩，或价格创近期新低但成交量放大，预示反转风险。使用20日价格区间位置与成交量相对位置之差，正值表示看跌背离，负值表示看涨背离，映射到[-1,1]。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 价格在20日区间内的位置 (0~1)
        min_20 = close.rolling(20).min()
        max_20 = close.rolling(20).max()
        price_position = (close - min_20) / (max_20 - min_20 + 1e-10)
        # 成交量在20日区间内的位置 (0~1)
        vol_min = volume.rolling(20).min()
        vol_max = volume.rolling(20).max()
        vol_position = (volume - vol_min) / (vol_max - vol_min + 1e-10)
        # 背离：价格位置高但成交量位置低 -> 负值(看跌背离)；价格位置低但成交量位置高 -> 正值(看涨背离)
        # 此处定义为 price_position - vol_position，然后映射到[-1,1]
        div = price_position - vol_position
        # 通常div在[-1,1]之间，直接输出
        result = pd.Series(np.clip(div, -1, 1), index=close.index)
        return result
