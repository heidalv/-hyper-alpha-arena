"""AI因子: 趋势稳定性因子 | 置信:70% | 计算最近N根K线的价格趋势强度与波动率的比值，用于识别趋势是否稳固。当趋势强且波动低时，因子接近+1，表示趋势可持续；当趋势弱或波动高时，因子接近-1，表示趋势脆弱易反转。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class TrendStability(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_trd_stab", name="TrendStability",
        display_name="趋势稳定性因子", description="计算最近N根K线的价格趋势强度与波动率的比值，用于识别趋势是否稳固。当趋势强且波动低时，因子接近+1，表示趋势可持续；当趋势弱或波动高时，因子接近-1，表示趋势脆弱易反转。",
        category="technical", subcategory="trend",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    # 假设data包含['open','high','low','close','volume']
    # 计算ATR (14周期)
    high = data['high']
    low = data['low']
    close = data['close']
    tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
    atr = tr.rolling(14).mean()
    # 计算20周期EMA趋势强度：价格与EMA的绝对偏离
    ema20 = close.ewm(span=20).mean()
    deviation = (close - ema20).abs()
    # 归一化：deviation / atr
    ratio = deviation / (atr + 1e-10)
    # 再取倒数并压缩到[-1,1]：使用sigmoid-like变换
    # 当ratio很小时表示趋势稳定，因子接近+1；ratio很大时趋势不稳定，因子接近-1
    # 使用2/(1+exp(ratio-1)) - 1 使得ratio=0时接近1，ratio=2时接近0，ratio>5时接近-1
    score = 2.0 / (1.0 + np.exp(ratio - 1.5)) - 1.0
    return score.clip(-1, 1)
