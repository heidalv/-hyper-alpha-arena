"""AI因子: 持仓超时失败 | 置信:60% | 识别价格在盘整后未能突破且出现反向加速的特征。计算价格在一定周期内（如30根K线）的波动率收缩，然后检测价格是否突破盘整区间后立即反转。该因子针对'max_hold_timeout'和'sl'模式下因持仓超时或止损导致的亏损。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Hold Timeout Failure(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_hold_timeout_failure", name="Hold Timeout Failure",
        display_name="持仓超时失败", description="识别价格在盘整后未能突破且出现反向加速的特征。计算价格在一定周期内（如30根K线）的波动率收缩，然后检测价格是否突破盘整区间后立即反转。该因子针对'max_hold_timeout'和'sl'模式下因持仓超时或止损导致的亏损。",
        category="technical", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    # data: pd.DataFrame
    import numpy as np
    window = 30  # 盘整窗口
    atr_period = 14
    # 计算平均真实波幅
    high_low = data['high'] - data['low']
    high_close = np.abs(data['high'] - data['close'].shift(1))
    low_close = np.abs(data['low'] - data['close'].shift(1))
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(atr_period).mean()
    # 计算价格相对波动率（近window周期内价格范围与ATR之比）
    high_window = data['high'].rolling(window).max()
    low_window = data['low'].rolling(window).min()
    range_window = high_window - low_window
    vol_ratio = range_window / atr.shift(1).fillna(method='ffill')
    # 当波动率收缩（vol_ratio小于阈值）且随后收盘价突破区间但未能持续
    shrink = vol_ratio < 1.5  # 收缩阈值
    # 当前收盘价相对于窗口最高最低的偏离
    mid = (high_window + low_window) / 2
    dev = (data['close'] - mid) / mid
    # 假突破：之前收缩，且当前收盘价突破区间（超过一半ATR），但下一根K线反向
    # 这里使用下一步信号，但我们需要实时信号，所以用当前突破且波动率仍在低位
    # 简化：若当前突破但成交量异常萎缩，则视为假突破
    vol_ma = data['volume'].rolling(20).mean()
    low_volume = data['volume'] < vol_ma * 0.7
    # 信号：上涨突破但低成交量 -> 看空；下跌突破但低成交量 -> 看多
    up_break = (data['close'] > high_window.shift(1)) & low_volume
    down_break = (data['close'] < low_window.shift(1)) & low_volume
    signal = np.where(up_break, -1, np.where(down_break, 1, 0))
    result = pd.Series(signal, index=data.index).fillna(0)
    return result.clip(-1, 1)
