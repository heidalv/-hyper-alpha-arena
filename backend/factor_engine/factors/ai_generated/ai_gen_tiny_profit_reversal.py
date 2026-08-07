"""AI因子: 微利反转因子 | 置信:60% | 捕捉近期收益极小（微小盈利或亏损）后价格出现反向突破的行情。若过去5日累计收益率绝对值小于1%，且今日价格反向突破该区间（向上突破此前5日高点或向下突破低点），则发出趋势延续信号（正向突破看多+1，反向突破看空-1），否则为0。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Tiny Profit Reversal Factor(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_tiny_profit_reversal", name="Tiny Profit Reversal Factor",
        display_name="微利反转因子", description="捕捉近期收益极小（微小盈利或亏损）后价格出现反向突破的行情。若过去5日累计收益率绝对值小于1%，且今日价格反向突破该区间（向上突破此前5日高点或向下突破低点），则发出趋势延续信号（正向突破看多+1，反向突破看空-1），否则为0。",
        category="composite", subcategory="momentum",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    close = data['close']
    high = data['high']
    low = data['low']
    # 过去5日累计收益率
    ret_5 = close.pct_change(5)
    # 判断微小收益区间
    tiny_range = (ret_5.abs() < 0.01)
    # 过去5日最高价与最低价
    high_5 = high.rolling(5).max()
    low_5 = low.rolling(5).min()
    # 今日收盘突破条件：突破前5日区间
    break_up = close > high_5.shift(1)  # 突破前5日最高（不含今日）
    break_down = close < low_5.shift(1)  # 突破前5日最低
    # 信号：当微小收益且突破时，方向与突破方向一致
    signal = pd.Series(0, index=data.index)
    signal[tiny_range & break_up] = 1.0
    signal[tiny_range & break_down] = -1.0
    return signal
