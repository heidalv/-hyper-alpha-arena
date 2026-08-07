"""AI因子: 微型冲击因子 | 置信:60% | 捕捉价格微小波动但成交量异常放大的情况，反映主力资金微小持仓变动导致的潜在风险。计算近期价格变化率与成交量变化的Z-score乘积，并取负值(异常放大为负向信号)。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Micro Shock Factor(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_micro_shock", name="Micro Shock Factor",
        display_name="微型冲击因子", description="捕捉价格微小波动但成交量异常放大的情况，反映主力资金微小持仓变动导致的潜在风险。计算近期价格变化率与成交量变化的Z-score乘积，并取负值(异常放大为负向信号)。",
        category="behavioral", subcategory="volume",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    # data: DataFrame with columns ['open','high','low','close','volume']
    close = data['close']
    volume = data['volume']
    # 价格微小变化率：当前close与前close的绝对百分比变化
    price_ret = close.pct_change().abs()
    # 成交量变化率
    vol_ret = volume.pct_change().abs()
    # 滚动窗口(20)的Z-score
    price_z = (price_ret - price_ret.rolling(20).mean()) / price_ret.rolling(20).std()
    vol_z = (vol_ret - vol_ret.rolling(20).mean()) / vol_ret.rolling(20).std()
    # 组合因子：价格变化小(负Z)且成交量变化大(正Z) => 负向信号
    factor = -price_z * vol_z
    # 归一化到[-1,1] 用tanh
    factor = factor.replace([np.inf, -np.inf], np.nan).fillna(0)
    return np.tanh(factor / 3.0)
