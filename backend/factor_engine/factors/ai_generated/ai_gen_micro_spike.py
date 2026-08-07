"""AI因子: 微小突破反转 | 置信:65% | 捕捉价格在近期窄幅区间内小幅突破近期极值（如20日高点或低点）后立即回撤的行为。当价格突破幅度<0.5%且随后3根K线内反向突破起始区间时，视为假突破风险。因子值接近+1表示高风险（微小突破后易反转），接近-1表示正常突破。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Micro Spike Reversal(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_micro_spike", name="Micro Spike Reversal",
        display_name="微小突破反转", description="捕捉价格在近期窄幅区间内小幅突破近期极值（如20日高点或低点）后立即回撤的行为。当价格突破幅度<0.5%且随后3根K线内反向突破起始区间时，视为假突破风险。因子值接近+1表示高风险（微小突破后易反转），接近-1表示正常突破。",
        category="behavioral", subcategory="contrarian",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    close = data['close']
    high = data['high']
    low = data['low']
    # 计算20日滚动最高最低
    rolling_high = high.rolling(20).max()
    rolling_low = low.rolling(20).min()
    # 定义突破阈值（0.5%）
    threshold = 0.005
    # 判断是否出现微小向上突破
    up_breakout = (close > rolling_high) & ((close - rolling_high) / rolling_high < threshold)
    # 计算突破后3根K线内的最低价
    future_low = low.shift(-3).rolling(3, min_periods=1).min()
    # 若未来最低跌破突破时的rolling_high，则视为反转
    up_reversal = up_breakout & (future_low < rolling_high)
    # 同理向下突破
    down_breakout = (close < rolling_low) & ((rolling_low - close) / rolling_low < threshold)
    future_high = high.shift(-3).rolling(3, min_periods=1).max()
    down_reversal = down_breakout & (future_high > rolling_low)
    # 信号计数，平滑处理
    signal = up_reversal.astype(int) - down_reversal.astype(int)
    # 转化为[-1,1]范围，使用滚动均值平滑
    result = signal.rolling(5, min_periods=1).mean().fillna(0)
    return pd.Series(result, index=data.index)
