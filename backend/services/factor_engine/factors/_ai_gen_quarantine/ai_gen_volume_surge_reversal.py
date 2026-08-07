"""AI因子: 成交量异常反转 | 置信:55% | 检测成交量异常放大（超过N日均值数倍），结合价格位置判断反转。如果在下跌过程中成交量激增且价格未创新低，则认为是底部反转信号（做多）；反之在上涨过程中成交量激增但价格未创新高，则为顶部反转（做空）。适合捕捉'unknown' regime下的反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Surge_Reversal(BaseFactor):
    """检测成交量异常放大（超过N日均值数倍），结合价格位置判断反转。如果在下跌过程中成交量激增且价格未创新低，则认为是底部反转信号（做多）；反之在上涨过程中成交量激增但价格未创新高，则为顶部反转（做空）。适合捕捉'unknown' regime下的反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_surge_reversal",
            name="Volume Surge Reversal",
            display_name="成交量异常反转",
            description="检测成交量异常放大（超过N日均值数倍），结合价格位置判断反转。如果在下跌过程中成交量激增且价格未创新低，则认为是底部反转信号（做多）；反之在上涨过程中成交量激增但价格未创新高，则为顶部反转（做空）。适合捕捉'unknown' regime下的反转。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 参数
        vol_lookback = 20
        vol_multiplier = 2.5
        price_lookback = 10

        volume = data['volume']
        close = data['close']
        high = data['high']
        low = data['low']

        # 成交量均值
        vol_ma = volume.rolling(vol_lookback).mean()
        vol_surge = volume > (vol_ma * vol_multiplier)

        # 价格区间
        recent_high = high.rolling(price_lookback).max()
        recent_low = low.rolling(price_lookback).min()

        # 当前位置：收盘价在区间内的相对位置
        price_range = recent_high - recent_low + 1e-10
        relative_position = (close - recent_low) / price_range  # 0~1

        # 反转条件：成交量激增且价格处于极值位置但未突破
        # 底部反转：成交量激增，价格处于低位 (relative_position < 0.3) 且未创新低
        bottom_reversal = vol_surge & (relative_position < 0.3) & (close > recent_low)
        # 顶部反转：成交量激增，价格处于高位 (relative_position > 0.7) 且未创新高
        top_reversal = vol_surge & (relative_position > 0.7) & (close < recent_high)

        # 信号强度：使用成交量倍数和位置偏离度
        vol_ratio = volume / (vol_ma + 1e-10)
        position_strength_bottom = (0.3 - relative_position) / 0.3
        position_strength_top = (relative_position - 0.7) / 0.3

        signal = pd.Series(0.0, index=data.index)
        signal[bottom_reversal] = vol_ratio[bottom_reversal] * position_strength_bottom[bottom_reversal]
        signal[top_reversal] = -vol_ratio[top_reversal] * position_strength_top[top_reversal]

        # 截断到[-1,1]（vol_ratio可能较大，但此处用clip保证范围）
        signal = signal.clip(-1.0, 1.0)
        return signal
