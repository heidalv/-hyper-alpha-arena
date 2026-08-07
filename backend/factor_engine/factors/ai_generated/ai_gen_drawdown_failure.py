"""AI因子: 回撤失败因子 | 置信:60% | 识别利润回撤后价格未能有效反弹或新低的情况。计算近期最大回撤比例与当前价格相对移动平均的位置，若回撤大且价格低于均线则给出负向信号。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Drawdown Failure Factor(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_drawdown_failure", name="Drawdown Failure Factor",
        display_name="回撤失败因子", description="识别利润回撤后价格未能有效反弹或新低的情况。计算近期最大回撤比例与当前价格相对移动平均的位置，若回撤大且价格低于均线则给出负向信号。",
        category="technical", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    close = data['close']
    high = data['high']
    low = data['low']
    # 滚动窗口(30)的最大回撤：1 - 当前close/滚动最高价
    rolling_max = high.rolling(30).max()
    drawdown = 1 - close / rolling_max
    # 价格相对20日均线位置： (close - ma20)/ma20
    ma20 = close.rolling(20).mean()
    price_dev = (close - ma20) / ma20
    # 组合：回撤大且价格低于均线 => 负向
    factor = -drawdown * (price_dev < 0).astype(float)
    factor = factor.fillna(0)
    return factor.clip(-1, 1)
