"""AI因子: 止损拥挤度因子 | 置信:60% | 通过价格与近期支撑阻力水平的关系，识别可能触发大量止损的区域。计算过去N天价格波动范围，并统计当前价格在历史高低点中的位置，以及近期成交量激增情况。若价格接近近期低点且成交量放大，则止损发生概率高，因子输出负值；反之远离关键位置或缩量时输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Stop_Loss_Congestion_Indicator(BaseFactor):
    """通过价格与近期支撑阻力水平的关系，识别可能触发大量止损的区域。计算过去N天价格波动范围，并统计当前价格在历史高低点中的位置，以及近期成交量激增情况。若价格接近近期低点且成交量放大，则止损发生概率高，因子输出负值；反之远离关键位置或缩量时输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_stop_cluster",
            name="Stop-Loss Congestion Indicator",
            display_name="止损拥挤度因子",
            description="通过价格与近期支撑阻力水平的关系，识别可能触发大量止损的区域。计算过去N天价格波动范围，并统计当前价格在历史高低点中的位置，以及近期成交量激增情况。若价格接近近期低点且成交量放大，则止损发生概率高，因子输出负值；反之远离关键位置或缩量时输出正值。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        N = 20
        # 近期高低点
        recent_high = high.rolling(N).max()
        recent_low = low.rolling(N).min()
        # 价格在近期区间的相对位置 (0~1)，0为最低，1为最高
        pos = (close - recent_low) / (recent_high - recent_low).replace(0, np.nan)
        # 成交量相对均值
        vol_ratio = volume / volume.rolling(N).mean()
        # 当价格接近近期低点（pos<0.2）且成交量放大（vol_ratio>1.5）时，认为止损密集，输出负值
        near_low = (pos < 0.2).astype(float)
        high_vol = (vol_ratio > 1.5).astype(float)
        # 因子：若触发条件则负，否则正
        factor = 1 - 2 * (near_low * high_vol)
        # 平滑处理
        factor = factor.rolling(3).mean().fillna(0)
        return factor.clip(-1,1)
