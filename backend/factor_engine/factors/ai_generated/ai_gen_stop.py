"""AI因子: 止损危险因子 | 置信:60% | 衡量价格相对于近期高点和低点的位置与波动率的关系，识别容易触发止损的反转区域。当价格接近极值且波动率增大时，反向概率高。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class StopDanger(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_stop", name="StopDanger",
        display_name="止损危险因子", description="衡量价格相对于近期高点和低点的位置与波动率的关系，识别容易触发止损的反转区域。当价格接近极值且波动率增大时，反向概率高。",
        category="composite", subcategory="contrarian",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    high = data['high']
    low = data['low']
    close = data['close']
    # 近期最大最小
    hh = high.rolling(10).max()
    ll = low.rolling(10).min()
    # 相对位置
    pos = (close - ll) / (hh - ll + 1e-10)
    # 波动率
    atr = (high - low).rolling(14).mean()
    atr_ratio = atr / close.rolling(20).mean()
    # 当位置接近极值且波动率高时，反转信号强
    # 接近上限（>0.8）且波动率高 -> 空头；接近下限（<0.2）且波动率高 -> 多头
    raw = (pos - 0.5) * 2 * atr_ratio
    # 平滑
    smooth = raw.rolling(3).mean()
    norm = smooth / (smooth.abs().rolling(20).mean() + 1e-10)
    return norm.clip(-1, 1)
