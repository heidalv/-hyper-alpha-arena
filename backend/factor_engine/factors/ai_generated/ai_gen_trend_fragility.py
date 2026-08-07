"""AI因子: 趋势脆弱性指数 | 置信:50% | 基于价格与移动平均线偏离度和波动率收缩，衡量当前趋势的脆弱程度。当趋势弱且波动率极低时，预示趋势可能突然反转，造成亏损。因子值-1为高脆弱性（极易反转），+1为强健趋势。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Trend_Fragility_Index(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_trend_fragility", name="Trend_Fragility_Index",
        display_name="趋势脆弱性指数", description="基于价格与移动平均线偏离度和波动率收缩，衡量当前趋势的脆弱程度。当趋势弱且波动率极低时，预示趋势可能突然反转，造成亏损。因子值-1为高脆弱性（极易反转），+1为强健趋势。",
        category="composite", subcategory="trend",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data: pd.DataFrame) -> pd.Series:
    import pandas as pd
    import numpy as np
    
    close = data['close']
    high = data['high']
    low = data['low']
    volume = data['volume']
    
    # 均线
    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()
    
    # 价格偏离均线程度（标准化）
    deviation = (close - ma20) / (ma20 + 1e-10)
    
    # 波动率收缩：当前ATR与近期ATR均值的比值
    tr = pd.concat([high - low,
                    (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    atr_ma = atr.rolling(50).mean()
    vol_ratio = atr / (atr_ma + 1e-10)
    
    # 成交量萎缩
    vol_ma = volume.rolling(20).mean()
    vol_ratio_v = volume / (vol_ma + 1e-10)
    
    # 趋势强度：斜率
    slope = (ma20 - ma20.shift(10)) / (ma20.shift(10) + 1e-10)
    
    # 脆弱性评分：偏离大但波动收缩且成交量萎缩
    fragility = -np.abs(deviation) * (1 - vol_ratio) * np.exp(-vol_ratio_v)
    # 趋势方向：如果偏离和斜率方向一致则强化，否则弱化
    trend_strength = np.tanh(deviation * slope * 10)
    
    factor = fragility * 0.6 + trend_strength * 0.4
    factor = factor.clip(-1, 1)
    return factor.fillna(0).round(6)
