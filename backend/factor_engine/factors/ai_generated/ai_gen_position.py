"""AI因子: 价格位置停滞因子 | 置信:60% | 检测收盘价处于近期区间中部且成交量萎缩的行情，预示缺乏突破动能，容易导致趋势跟踪策略亏损。值越接近+1，价格越处于区间中间且成交量低迷。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Position Stagnation(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_position", name="Position Stagnation",
        display_name="价格位置停滞因子", description="检测收盘价处于近期区间中部且成交量萎缩的行情，预示缺乏突破动能，容易导致趋势跟踪策略亏损。值越接近+1，价格越处于区间中间且成交量低迷。",
        category="technical", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    import pandas as pd
    n = 20
    high = data['high'].rolling(n).max()
    low = data['low'].rolling(n).min()
    pos = (data['close'] - low) / (high - low).replace(0, np.nan)
    center = 1 - 2 * np.abs(pos - 0.5)  # 0-1，中间为1，两端为0
    vol_ma = data['volume'].rolling(n).mean()
    vol_ratio = data['volume'] / vol_ma.replace(0, np.nan)
    # 成交量萎缩时vol_ratio<1，取对数或线性映射
    vol_factor = 1 - vol_ratio.clip(0, 2)  # 当vol_ratio=0时1，=1时0，=2时-1，但需要clip
    vol_factor = vol_factor.clip(-1, 1)
    score = 0.5 * center + 0.5 * vol_factor
    # 映射到[-1,1] (score范围-0.5~1)
    result = 2 * score - 0.5  # 但需重新映射? 使用tanh调整
    # 直接裁剪
    result = result.clip(-1, 1)
    return result.fillna(0)
