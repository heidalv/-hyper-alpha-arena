"""AI因子: 微观反转因子 | 置信:60% | 基于短期价格动量与成交量的异常判断反转。当过去3根K线的平均涨跌幅绝对值超过阈值且当前成交量放大至近20日均值1.5倍以上时，产生反向信号。信号强度由当前价格相对于过去N日价格区间的分位数决定。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Micro Reversal(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_micro_rev", name="Micro Reversal",
        display_name="微观反转因子", description="基于短期价格动量与成交量的异常判断反转。当过去3根K线的平均涨跌幅绝对值超过阈值且当前成交量放大至近20日均值1.5倍以上时，产生反向信号。信号强度由当前价格相对于过去N日价格区间的分位数决定。",
        category="behavioral", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    ret = data['close'].pct_change()
    avg_abs_ret = ret.abs().rolling(3).mean()
    vol_ratio = data['volume'] / data['volume'].rolling(20).mean()
    # 价格相对于过去20日区间的位置
    hh = data['high'].rolling(20).max()
    ll = data['low'].rolling(20).min()
    range_pos = (data['close'] - ll) / (hh - ll + 1e-10)
    # 信号：短期动量大且成交量异常，则反向信号
    condition = (avg_abs_ret > avg_abs_ret.rolling(50).quantile(0.8)) & (vol_ratio > 1.5)
    signal = np.where(condition, 2 * (0.5 - range_pos), 0)
    return pd.Series(np.clip(signal, -1, 1), index=data.index)
