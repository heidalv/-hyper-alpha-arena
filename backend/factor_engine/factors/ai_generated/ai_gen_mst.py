"""AI因子: 微小止损触发 | 置信:55% | 捕捉价格长时间紧贴移动平均线后突然偏离的微小止损模式。计算收盘价与20日均线的相对差绝对值，当过去5日该值均小于0.5%且最新偏离超过1%时，若向上偏离则看跌（空头止损），向下偏离则看涨（多头止损）。返回-1表示看跌，+1看涨，0无信号。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Micro_Stop_Trigger(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_mst", name="Micro_Stop_Trigger",
        display_name="微小止损触发", description="捕捉价格长时间紧贴移动平均线后突然偏离的微小止损模式。计算收盘价与20日均线的相对差绝对值，当过去5日该值均小于0.5%且最新偏离超过1%时，若向上偏离则看跌（空头止损），向下偏离则看涨（多头止损）。返回-1表示看跌，+1看涨，0无信号。",
        category="technical", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    # 均线
    ma20 = data['close'].rolling(20).mean()
    # 相对偏离
    dev_pct = np.abs(data['close'] - ma20) / (ma20 + 1e-10)
    # 过去5日几乎都在0.5%以内
    tight = (dev_pct.rolling(5).max() < 0.005).astype(int)
    # 最新偏离超过1%
    big_dev = data['close'] - ma20
    sign = np.sign(big_dev)
    threshold = 0.01 * ma20
    signal = pd.Series(np.where(tight & (np.abs(big_dev) > threshold), -sign, 0.0))
    return signal
