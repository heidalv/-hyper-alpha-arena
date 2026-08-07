"""AI因子: 微小变动放量因子 | 置信:65% | 当价格变动很小（如收盘变动小于过去N周期平均绝对变动的0.5倍）但成交量异常放大（高于过去N周期均值1.5倍）时，认为是趋势乏力的反转信号，因子值为负（看跌）；否则为0。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class TinyMoveHighVolume(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_tiny", name="TinyMoveHighVolume",
        display_name="微小变动放量因子", description="当价格变动很小（如收盘变动小于过去N周期平均绝对变动的0.5倍）但成交量异常放大（高于过去N周期均值1.5倍）时，认为是趋势乏力的反转信号，因子值为负（看跌）；否则为0。",
        category="behavioral", subcategory="volume",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    # 计算收盘价变动
    price_change = data['close'].diff()
    # 过去20周期平均绝对变动
    avg_abs_change = price_change.abs().rolling(20, min_periods=10).mean()
    # 成交量均值
    avg_volume = data['volume'].rolling(20, min_periods=10).mean()
    tiny = (price_change.abs() < 0.5 * avg_abs_change).astype(float)
    high_vol = (data['volume'] > 1.5 * avg_volume).astype(float)
    signal = tiny * high_vol
    # 信号为正时价格变动方向？这里取相反方向：如果价格涨但量异动则看跌，跌则看涨？根据亏损模式多为反转，统一看跌
    # 这里简单：出现信号时值为-1（看跌），否则0
    result = -signal
    return result.clip(-1, 1)
