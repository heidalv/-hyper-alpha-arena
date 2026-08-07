"""AI因子: 流动性衰竭反转 | 置信:45% | 综合成交量衰减和价格极值，识别市场流动性枯竭后的反转。计算成交量连续收缩与价格创近期新高/新低的重叠，做空在高位成交量萎缩的品种，做多在低位成交量萎缩的品种。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityExhaustionReversal(BaseFactor):
    """综合成交量衰减和价格极值，识别市场流动性枯竭后的反转。计算成交量连续收缩与价格创近期新高/新低的重叠，做空在高位成交量萎缩的品种，做多在低位成交量萎缩的品种。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liq_exhaustion",
            name="Liquidity Exhaustion Reversal",
            display_name="流动性衰竭反转",
            description="综合成交量衰减和价格极值，识别市场流动性枯竭后的反转。计算成交量连续收缩与价格创近期新高/新低的重叠，做空在高位成交量萎缩的品种，做多在低位成交量萎缩的品种。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 参数
        window = 14
        volume_window = 10
        # 价格极值
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        # 最近N周期最高价和最低价
        recent_high = high.rolling(window=window).max()
        recent_low = low.rolling(window=window).min()
        # 判断是否创近期新高/新低（接近新高/新低）
        near_high = (close >= recent_high * 0.98).astype(float)
        near_low = (close <= recent_low * 1.02).astype(float)
        # 成交量连续萎缩：过去volume_window天内成交量递减
        vol_lag1 = volume.shift(1)
        vol_lag2 = volume.shift(2)
        vol_decreasing = (volume < vol_lag1) & (vol_lag1 < vol_lag2)
        vol_decreasing = vol_decreasing.astype(float)
        # 组合信号
        bearish = near_high * vol_decreasing
        bullish = near_low * vol_decreasing
        factor = -bearish + bullish
        return factor
