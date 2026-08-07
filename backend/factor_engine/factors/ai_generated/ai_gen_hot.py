"""AI因子: 持仓超时风险 | 置信:60% | 检测收盘价在20日均线附近（偏离度绝对值<1%）的K线在最近10根中的占比，若占比>0.5且波动率（标准差）高于1%，表明价格长时间盘整无趋势，容易触发持仓超时亏损，输出负向信号。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Holding Timeout Risk(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_hot", name="Holding Timeout Risk",
        display_name="持仓超时风险", description="检测收盘价在20日均线附近（偏离度绝对值<1%）的K线在最近10根中的占比，若占比>0.5且波动率（标准差）高于1%，表明价格长时间盘整无趋势，容易触发持仓超时亏损，输出负向信号。",
        category="behavioral", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    close = data['close']
    ma20 = close.rolling(20).mean()
    dev = (close - ma20) / ma20
    near_ma = (dev.abs() < 0.01).rolling(10).sum() / 10.0
    vol = close.pct_change().rolling(10).std()
    condition = (near_ma > 0.5) & (vol > 0.01)
    result = pd.Series(1.0, index=data.index)
    result[condition] = -1.0
    return result
