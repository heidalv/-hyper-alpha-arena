"""AI因子: 成交量确认突破失败 | 置信:50% | 价格突破关键水平但成交量未放大时容易假突破导致亏损。该因子计算价格突破20日高低点时的成交量变化率，成交量萎缩时输出负值预警。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeConfirmationBreakoutFailure(BaseFactor):
    """价格突破关键水平但成交量未放大时容易假突破导致亏损。该因子计算价格突破20日高低点时的成交量变化率，成交量萎缩时输出负值预警。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_breakout",
            name="Volume Confirmation Breakout Failure",
            display_name="成交量确认突破失败",
            description="价格突破关键水平但成交量未放大时容易假突破导致亏损。该因子计算价格突破20日高低点时的成交量变化率，成交量萎缩时输出负值预警。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        volume = data['volume']
        high = data['high']
        low = data['low']
        # 20日最高最低
        high20 = high.rolling(20).max()
        low20 = low.rolling(20).min()
        # 当前价格是否突破前高/前低
        break_up = close > high20.shift(1)
        break_down = close < low20.shift(1)
        # 成交量变化率(相对于20日均量)
        vol_ma20 = volume.rolling(20).mean()
        vol_ratio = volume / (vol_ma20 + 1e-10)
        # 突破时成交量不足则负，否则正
        # 使用vol_ratio偏离1的程度，突破时若小于1则危险
        signal = pd.Series(np.nan, index=close.index)
        signal[break_up] = 2 * (vol_ratio[break_up] - 0.5) - 1  # 映射到[-1,1]
        signal[break_down] = -2 * (vol_ratio[break_down] - 0.5) + 1  # 向下突破同理
        # 未突破时中性0
        signal = signal.fillna(0)
        return signal.clip(-1, 1)
