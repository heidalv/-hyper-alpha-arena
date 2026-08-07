"""AI因子: 微小变动风险 | 置信:60% | 检测最近5根K线中收盘价连续微小波动（涨跌幅绝对值小于0.5%）的次数占比，若占比>0.6且收盘价低于20日均线，则触发负向信号，表示累积回调风险高，容易导致小止损亏损。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Small Movement Risk(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_smr", name="Small Movement Risk",
        display_name="微小变动风险", description="检测最近5根K线中收盘价连续微小波动（涨跌幅绝对值小于0.5%）的次数占比，若占比>0.6且收盘价低于20日均线，则触发负向信号，表示累积回调风险高，容易导致小止损亏损。",
        category="behavioral", subcategory="momentum",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    # data: DataFrame with columns ['close']
    close = data['close']
    pct_change = close.pct_change().abs()
    small_move = (pct_change < 0.005).rolling(5).sum() / 5.0
    ma20 = close.rolling(20).mean()
    condition = (small_move > 0.6) & (close < ma20)
    result = pd.Series(1.0, index=data.index)
    result[condition] = -1.0
    return result
