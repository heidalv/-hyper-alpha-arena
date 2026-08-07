"""AI因子: 量价背离因子 | 置信:55% | 计算价格变动与成交量变动之间的短期相关性，当两者负相关或相关性很弱时，市场状态可能不明（regime=unknown），因子值接近-1提示量价背离。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """计算价格变动与成交量变动之间的短期相关性，当两者负相关或相关性很弱时，市场状态可能不明（regime=unknown），因子值接近-1提示量价背离。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_div",
            name="Volume-Price Divergence",
            display_name="量价背离因子",
            description="计算价格变动与成交量变动之间的短期相关性，当两者负相关或相关性很弱时，市场状态可能不明（regime=unknown），因子值接近-1提示量价背离。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 价格变化率
        ret = close.pct_change()
        # 成交量变化率
        vol_change = volume.pct_change()
        # 滚动20期相关系数
        corr = ret.rolling(20).corr(vol_change)
        # 映射到[-1,1]，负相关或低正相关为负值
        result = corr.fillna(0)
        return result.clip(-1, 1)
