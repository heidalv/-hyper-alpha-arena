"""AI因子: 波动率价差因子 | 置信:60% | 衡量短期波动率相对于长期波动率的收缩程度，当短期波动率显著低于长期时预示市场进入低波动期，容易因微小波动触发止损或过早平仓。使用5日波动率与50日波动率之比。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volatility Spread Factor(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_volatility_spread", name="Volatility Spread Factor",
        display_name="波动率价差因子", description="衡量短期波动率相对于长期波动率的收缩程度，当短期波动率显著低于长期时预示市场进入低波动期，容易因微小波动触发止损或过早平仓。使用5日波动率与50日波动率之比。",
        category="composite", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    close = data['close']
    log_ret = np.log(close / close.shift(1))
    vol_short = log_ret.rolling(5).std()
    vol_long = log_ret.rolling(50).std()
    spread = vol_short / (vol_long + 1e-10)
    # normalize: when spread < 0.8 → negative regime
    result = -1 * (1 - spread)  # if spread=0.5 → -0.5
    # cap between -1 and 1
    result = result.clip(-1, 1)
    return result
