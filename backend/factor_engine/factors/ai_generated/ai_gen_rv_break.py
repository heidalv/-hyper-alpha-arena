"""AI因子: 低波动突破反转因子 | 置信:60% | 通过计算近期ATR与价格变动比率，结合成交量确认，识别在低波动区间后出现的假突破。当价格短期突破但成交量未有效放大时，预示反转风险。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Reversal after Low Volatility Breakout(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_rv_break", name="Reversal after Low Volatility Breakout",
        display_name="低波动突破反转因子", description="通过计算近期ATR与价格变动比率，结合成交量确认，识别在低波动区间后出现的假突破。当价格短期突破但成交量未有效放大时，预示反转风险。",
        category="composite", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    # 参数
    atr_period = 14
    vol_period = 20
    threshold = 0.5
    
    high = data['high']
    low = data['low']
    close = data['close']
    volume = data['volume']
    
    # ATR
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(atr_period).mean()
    
    # 价格变动比率（与前一收盘相比的百分比）
    pct_change = close.pct_change()
    
    # 成交量均值与当前量比
    vol_ma = volume.rolling(vol_period).mean()
    vol_ratio = volume / vol_ma
    
    # 信号：价格变动小（低波动），但成交量极低，随后出现突破信号？
    # 实际构造：计算最近N根K线的ATR与价格变动标准差之比
    # 简化：使用ATR与近期价格变动绝对值之比，若比值大说明波动小
    atr_ratio = atr / (close * 0.01 + 1e-10)  # ATR相对于价格的百分比
    
    # 条件：价格小幅波动（低波动），并且成交量萎缩
    low_volatility = atr_ratio < atr_ratio.rolling(50).mean() * threshold
    low_volume = vol_ratio < 0.7
    
    # 再结合短期价格方向：如果低波动后价格突然突破（如close突破前高/前低），但成交量未跟上，则看空/看多
    # 计算突破信号：close突破前10日高点/低点
    rolling_high = high.rolling(10).max()
    rolling_low = low.rolling(10).min()
    
    breakout_up = (close > rolling_high.shift()) & (close.shift() <= rolling_high.shift())
    breakout_down = (close < rolling_low.shift()) & (close.shift() >= rolling_low.shift())
    
    # 假突破：低波动+成交量低+突破
    false_break_up = breakout_up & low_volatility & low_volume
    false_break_down = breakout_down & low_volatility & low_volume
    
    # 转化为-1到1：假突破向上则做空（-1），向下则做多（+1）
    factor = pd.Series(0, index=data.index)
    factor[false_break_up] = -1.0
    factor[false_break_down] = 1.0
    
    # 平滑处理：使用滚动平均
    result = factor.rolling(3).mean().fillna(0)
    return result
