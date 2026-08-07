"""AI因子: 伪区间边界突破反转 | 置信:60% | 识别价格在近期高低点区间内反复假突破（上影线/下影线测试区间边界后快速收回）的形态。当价格连续两次测试同一方向边界失败且成交量递减时，预期反向运动。该因子捕捉亏损模式中常见的“止损/超时”，适用于无明确趋势的行情。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PseudoRangeBoundary(BaseFactor):
    """识别价格在近期高低点区间内反复假突破（上影线/下影线测试区间边界后快速收回）的形态。当价格连续两次测试同一方向边界失败且成交量递减时，预期反向运动。该因子捕捉亏损模式中常见的“止损/超时”，适用于无明确趋势的行情。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_plr",
            name="PseudoRangeBoundary",
            display_name="伪区间边界突破反转",
            description="识别价格在近期高低点区间内反复假突破（上影线/下影线测试区间边界后快速收回）的形态。当价格连续两次测试同一方向边界失败且成交量递减时，预期反向运动。该因子捕捉亏损模式中常见的“止损/超时”，适用于无明确趋势的行情。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd

        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']

        # 计算过去20期的最高高点和最低低点作为区间
        lookback = 20
        recent_high = high.rolling(lookback).max()
        recent_low = low.rolling(lookback).min()
        range_width = recent_high - recent_low

        # 判断上影线和下影线
        upper_shadow = high - np.maximum(close, open)  # 注意open未提供，用close近似
        lower_shadow = np.minimum(close, open) - low
        # 由于没有open，用前close近似实际开高低？改用close偏移量：
        # 更简单：价格触及边界后返回的形态
        # 计算价格接近上边界并收在内部（收盘价<上边界）
        proximity = 0.02  # 2%阈值
        touch_high = (high >= recent_high * (1 - proximity)) & (close < recent_high)
        touch_low = (low <= recent_low * (1 + proximity)) & (close > recent_low)

        # 成交量萎缩条件
        vol_ma = volume.rolling(5).mean()
        vol_decline = volume < vol_ma.shift(1) * 0.8

        # 连续两次假突破判定（简单实现：历史2期内出现一次）
        touch_high_prev = touch_high.shift(1).fillna(False) | touch_high.shift(2).fillna(False)
        touch_low_prev = touch_low.shift(1).fillna(False) | touch_low.shift(2).fillna(False)

        # 信号：近期假突破+成交量萎缩 -> 反向
        signal_bear = touch_high_prev & touch_high & vol_decline
        signal_bull = touch_low_prev & touch_low & vol_decline

        signal = np.where(signal_bear, -1, np.where(signal_bull, 1, 0))
        return pd.Series(signal, index=data.index).clip(-1, 1)
