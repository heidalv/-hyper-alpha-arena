"""AI因子: 日内强度因子 | 置信:60% | 利用开盘价、最高最低价和收盘价的关系衡量多空力量，结合成交量异常识别虚假突破。当日内价格突破前一日区间但收盘回归，且成交量放大时，判断为假突破并给出反向信号。计算 (close - open) / (high - low) 的标准化值，并乘以成交量异常系数。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Intraday Strength(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_intra_str", name="Intraday Strength",
        display_name="日内强度因子", description="利用开盘价、最高最低价和收盘价的关系衡量多空力量，结合成交量异常识别虚假突破。当日内价格突破前一日区间但收盘回归，且成交量放大时，判断为假突破并给出反向信号。计算 (close - open) / (high - low) 的标准化值，并乘以成交量异常系数。",
        category="behavioral", subcategory="contrarian",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    hl = data['high'] - data['low'] + 1e-10
    intra_pos = (data['close'] - data['open']) / hl
    # 成交量相对20日均值的比率
    vol_ratio = data['volume'] / data['volume'].rolling(20).mean()
    # 价格突破前一日高/低点？使用前一日收盘价和range
    prev_close = data['close'].shift(1)
    prev_range = data['high'].shift(1) - data['low'].shift(1)
    # 突破条件：今日最低低于前日最低且收盘高于前日最低（假突破向下）
    break_down = (data['low'] < data['low'].shift(1)) & (data['close'] > data['low'].shift(1))
    break_up = (data['high'] > data['high'].shift(1)) & (data['close'] < data['high'].shift(1))
    # 合成信号：假突破时取日内位置的反向
    signal = np.where(break_down, -1.0 * np.clip(vol_ratio * intra_pos, -1, 1),
                      np.where(break_up, -1.0 * np.clip(vol_ratio * intra_pos, -1, 1), 0))
    return pd.Series(signal, index=data.index)
