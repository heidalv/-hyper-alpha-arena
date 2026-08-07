"""AI因子: 市场混沌指数 | 置信:60% | 基于近期价格区间与ATR的比值计算震荡强度，值越高表示市场越混沌（震荡），适合识别regime=unknown的状态。输出[-1, +1]，+1表示极强震荡，-1表示清晰趋势。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Choppiness_Index(BaseFactor):
    """基于近期价格区间与ATR的比值计算震荡强度，值越高表示市场越混沌（震荡），适合识别regime=unknown的状态。输出[-1, +1]，+1表示极强震荡，-1表示清晰趋势。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_choppy",
            name="Choppiness Index",
            display_name="市场混沌指数",
            description="基于近期价格区间与ATR的比值计算震荡强度，值越高表示市场越混沌（震荡），适合识别regime=unknown的状态。输出[-1, +1]，+1表示极强震荡，-1表示清晰趋势。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 参数
        period = 14
        # 计算ATR
        tr = np.maximum(data['high'] - data['low'],
                        np.maximum(abs(data['high'] - data['close'].shift(1)),
                                   abs(data['low'] - data['close'].shift(1))))
        atr = tr.rolling(period).mean()
        # 计算价格区间
        high_max = data['high'].rolling(period).max()
        low_min = data['low'].rolling(period).min()
        range_sum = (high_max - low_min)
        # 避免除以0
        atr = atr.replace(0, np.nan)
        choppiness = range_sum / (atr * period)  # 原始CI通常在0~100之间
        # 标准化到[-1,1]
        # 通常CI超过70为震荡，低于30为趋势，使用线性映射
        norm = (choppiness - 50) / 50  # 假设均值50
        norm = norm.clip(-1, 1)
        return norm.fillna(0)
