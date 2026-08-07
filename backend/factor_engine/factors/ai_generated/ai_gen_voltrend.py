"""AI因子: 成交量调整趋势强度 | 置信:65% | 判断价格趋势是否得到成交量支持。当价格上涨但成交量萎缩时，表明趋势可能脆弱，容易反转；当价格下跌且成交量放大时，可能为真实下跌。计算收盘价相对于短期均线的方向与成交量变化率的乘积，归一化到[-1,1]，正向值表示健康趋势，负向值表示虚假趋势。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volume_Adjusted_Trend_Strength(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_voltrend", name="Volume_Adjusted_Trend_Strength",
        display_name="成交量调整趋势强度", description="判断价格趋势是否得到成交量支持。当价格上涨但成交量萎缩时，表明趋势可能脆弱，容易反转；当价格下跌且成交量放大时，可能为真实下跌。计算收盘价相对于短期均线的方向与成交量变化率的乘积，归一化到[-1,1]，正向值表示健康趋势，负向值表示虚假趋势。",
        category="composite", subcategory="momentum",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    close = data['close']
    volume = data['volume']
    # 短期均线 (5周期)
    short_ma = close.rolling(5).mean()
    # 价格方向: 收盘价与均线差
    price_dir = (close - short_ma) / close.replace(0, 1e-10)
    # 成交量变化率 (5期百分比变化)
    vol_change = volume.pct_change(5).fillna(0)
    # 成交量调整: 当价格与成交量同向时强化
    raw = price_dir * np.sign(vol_change) * np.abs(vol_change).clip(0, 1)
    # 填充NaN，归一化到[-1,1]
    result = raw.replace([np.inf, -np.inf], np.nan).fillna(0)
    result = result.clip(-1, 1)
    return result
