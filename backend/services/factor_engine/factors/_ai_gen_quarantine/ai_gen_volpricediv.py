"""AI因子: 量价背离度 | 置信:60% | 衡量成交量变化与价格变化方向的一致性。当成交量萎缩且价格波动时，容易出现假突破（导致master_running_close_tiny等亏损）；量价背离越严重，因子值越负。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """衡量成交量变化与价格变化方向的一致性。当成交量萎缩且价格波动时，容易出现假突破（导致master_running_close_tiny等亏损）；量价背离越严重，因子值越负。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volpricediv",
            name="VolumePriceDivergence",
            display_name="量价背离度",
            description="衡量成交量变化与价格变化方向的一致性。当成交量萎缩且价格波动时，容易出现假突破（导致master_running_close_tiny等亏损）；量价背离越严重，因子值越负。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            import numpy as np
            close = data['close']
            volume = data['volume']
            # 价格变化率
            price_ret = close.pct_change()
            # 成交量变化率
            vol_ret = volume.pct_change()
            # 滚动相关系数（10期）
            corr = price_ret.rolling(10).corr(vol_ret)
            # 当相关系数低或负时，量价背离，输出负；正相关输出正
            result = -corr  # 负相关意味着背离，取负号使得背离为负值
            result = result.fillna(0)
            # 截断到[-1,1]
            result = np.clip(result, -1, 1)
            return result
