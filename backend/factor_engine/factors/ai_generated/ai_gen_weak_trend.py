"""AI因子: 弱趋势识别 | 置信:65% | 通过计算14周期ADX（平均趋向指数）来判断市场趋势强度。当ADX低于25时，市场处于无趋势或震荡状态，此时开仓容易亏损，因子输出负值；ADX高于50时趋势强劲，输出正值。值域[-1,1]线性映射。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Weak Trend ADX(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_weak_trend", name="Weak Trend ADX",
        display_name="弱趋势识别", description="通过计算14周期ADX（平均趋向指数）来判断市场趋势强度。当ADX低于25时，市场处于无趋势或震荡状态，此时开仓容易亏损，因子输出负值；ADX高于50时趋势强劲，输出正值。值域[-1,1]线性映射。",
        category="technical", subcategory="trend",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    # 计算TR
    high, low, close = data['high'], data['low'], data['close']
    tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
    atr = tr.rolling(14).mean()
    # 计算+DM和-DM
    up = high - high.shift(1)
    down = low.shift(1) - low
    pos_dm = np.where((up > down) & (up > 0), up, 0)
    neg_dm = np.where((down > up) & (down > 0), down, 0)
    # 平滑
    pos_dm_s = pd.Series(pos_dm).rolling(14).mean()
    neg_dm_s = pd.Series(neg_dm).rolling(14).mean()
    pdi = 100 * pos_dm_s / atr
    ndi = 100 * neg_dm_s / atr
    dx = 100 * abs(pdi - ndi) / (pdi + ndi + 1e-10)
    adx = dx.rolling(14).mean()
    # 映射到[-1,1]，ADX通常0-100，低于25为弱趋势，高于50为强趋势
    factor = 2 * (adx - 25) / (50 - 25) - 1
    factor = factor.clip(-1, 1)
    return factor
