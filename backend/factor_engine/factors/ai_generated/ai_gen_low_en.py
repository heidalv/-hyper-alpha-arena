"""AI因子: 低能量盘整检测器 | 置信:55% | 通过价格变动幅度与成交量变动的比值量化市场活性。当市场处于窄幅震荡且成交量萎缩时，容易出现假突破，导致做空止损。因子值[-1,1]：正值表示低能量盘整，应避免开仓；负值表示正常能量，可交易。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Low Energy Consolidation Detector(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_low_en", name="Low Energy Consolidation Detector",
        display_name="低能量盘整检测器", description="通过价格变动幅度与成交量变动的比值量化市场活性。当市场处于窄幅震荡且成交量萎缩时，容易出现假突破，导致做空止损。因子值[-1,1]：正值表示低能量盘整，应避免开仓；负值表示正常能量，可交易。",
        category="composite", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    # 参数
    window = 10
    # 价格变动幅度：最高-最低相对收盘价
    price_range = (data['high'] - data['low']) / (data['close'] + 1e-10)
    # 成交量变动：成交量变化率（当前/均值）
    vol_ma = data['volume'].rolling(window, min_periods=1).mean()
    vol_ratio = data['volume'] / (vol_ma + 1e-10)
    # 能量指标 = 价格变动幅度 * 成交量比率（高幅*高量=高能量）
    energy = price_range * vol_ratio
    # 标准化到[-1,1]：取倒数并使用tanh包装
    # 低能量时 energy小，我们想要正值表示低能量，所以用 -tanh(energy-阈值)
    # 简便：将energy标准化后取负
    norm_energy = (energy - energy.rolling(window, min_periods=1).mean()) / (energy.rolling(window, min_periods=1).std() + 1e-10)
    result = -np.tanh(norm_energy * 0.5)
    result = result.fillna(0)
    return pd.Series(result, index=data.index)
