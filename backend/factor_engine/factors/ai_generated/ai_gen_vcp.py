"""AI因子: 成交量确认回调因子 | 置信:65% | 捕捉价格小幅回调但成交量萎缩的强势信号，避免在放量下跌时追多。计算当前收盘价相对于近N日高点的位置，乘以成交量相对均值的比率，并用价格波动率调整，最终归一化到[-1,1]。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volume Confirmation Pullback(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_vcp", name="Volume Confirmation Pullback",
        display_name="成交量确认回调因子", description="捕捉价格小幅回调但成交量萎缩的强势信号，避免在放量下跌时追多。计算当前收盘价相对于近N日高点的位置，乘以成交量相对均值的比率，并用价格波动率调整，最终归一化到[-1,1]。",
        category="composite", subcategory="momentum",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data: pd.DataFrame) -> pd.Series:
    import numpy as np
    n = 10
    high = data['high'].rolling(n).max()
    ret = (data['close'] - high) / (high - data['low'].rolling(n).min() + 1e-10)
    vol_ratio = data['volume'] / data['volume'].rolling(n).mean()
    atr = (data['high'] - data['low']).rolling(14).mean().replace(0, 1e-10)
    raw = ret * (1 - vol_ratio) / atr
    return np.clip(raw, -1, 1)
