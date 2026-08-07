"""AI因子: 量价背离因子 | 置信:60% | 价格创出近期新高，但成交量未能同步放大，形成顶背离，预示买盘不足，多头容易遭遇反转亏损（如master_running或max_hold_timeout）。因子值接近+1表示背离程度高（危险），接近-1表示量价配合健康。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格创出近期新高，但成交量未能同步放大，形成顶背离，预示买盘不足，多头容易遭遇反转亏损（如master_running或max_hold_timeout）。因子值接近+1表示背离程度高（危险），接近-1表示量价配合健康。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vp_div",
            name="Volume-Price Divergence",
            display_name="量价背离因子",
            description="价格创出近期新高，但成交量未能同步放大，形成顶背离，预示买盘不足，多头容易遭遇反转亏损（如master_running或max_hold_timeout）。因子值接近+1表示背离程度高（危险），接近-1表示量价配合健康。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        volume = data['volume']
        # 20日最高价
        highest_20 = close.rolling(20).max()
        # 价格是否处于近期高位（距离最高价<0.5%）
        near_high = (close >= highest_20 * 0.995).astype(float)
        # 成交量相对强弱
        vol_ratio = volume / volume.rolling(20).mean()
        # 背离信号：价格高位，但成交量低于1倍均值，背离程度为(1 - vol_ratio)
        divergence = near_high * (1.0 - vol_ratio.clip(upper=2.0))
        # 用滚动Z-score平滑
        z = (divergence - divergence.rolling(60).mean()) / divergence.rolling(60).std()
        # 映射到[-1,1]
        result = np.tanh(z / 2.0)
        return result
