"""AI因子: 动量波动比 | 置信:70% | 计算短期价格动量（N日收益率）与同期ATR波动率的比值，当比值绝对值大且方向向上时，趋势强劲，做空易亏损。输出经tanh归一化至[-1,1]，正值表示上行趋势强，负值表示下行趋势强。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Momentum-Volatility Ratio(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_momvol", name="Momentum-Volatility Ratio",
        display_name="动量波动比", description="计算短期价格动量（N日收益率）与同期ATR波动率的比值，当比值绝对值大且方向向上时，趋势强劲，做空易亏损。输出经tanh归一化至[-1,1]，正值表示上行趋势强，负值表示下行趋势强。",
        category="composite", subcategory="momentum",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    # 参数
    n = 10
    # 计算收益率
    ret = data['close'].pct_change(n)
    # 计算ATR
    high = data['high']
    low = data['low']
    close = data['close']
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(n).mean()
    # 避免除零
    atr_safe = atr.replace(0, np.nan)
    ratio = ret / (atr_safe / close.shift())
    # 归一化到[-1,1]
    result = np.tanh(ratio * 2)  # 乘2放大敏感度
    return result.fillna(0)
