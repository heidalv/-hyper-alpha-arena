"""AI因子: 假突破因子 | 置信:60% | 检测假突破：当价格突破近期最高价（前10根K线）时，若收盘价未能维持在高位（回落到中位以下），且成交量显著放大（超过20日均量的1.5倍），则视为假突破信号，输出-1；否则输出+1。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class fake_breakout(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_fake_breakout", name="fake_breakout",
        display_name="假突破因子", description="检测假突破：当价格突破近期最高价（前10根K线）时，若收盘价未能维持在高位（回落到中位以下），且成交量显著放大（超过20日均量的1.5倍），则视为假突破信号，输出-1；否则输出+1。",
        category="behavioral", subcategory="contrarian",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    import pandas as pd
    
    high = data['high'].values
    close = data['close'].values
    volume = data['volume'].values
    
    lookback = 10
    # 前N根K线的最高价
    rolling_max = pd.Series(high).rolling(window=lookback).max().shift(1).values  # 避免包含当前K线
    # 当前最高价是否超过前高
    break_high = high > rolling_max
    
    # 当前K线的中位价 (high+low)/2，简化用开盘+收盘？使用实际中位
    low = data['low'].values
    mid = (high + low) / 2.0
    close_below_mid = close < mid
    
    # 成交量放大条件
    vol_ma20 = pd.Series(volume).rolling(window=20).mean().values
    vol_surge = volume > vol_ma20 * 1.5
    
    # 综合判断假突破：突破+收盘回落到中位以下+成交量放大
    fake = break_high & close_below_mid & vol_surge
    
    # 返回-1代表假突破预警，否则+1
    result = np.where(fake, -1.0, 1.0)
    return pd.Series(result, index=data.index)
