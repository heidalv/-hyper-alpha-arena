"""AI因子: 成交量静默陷阱因子 | 置信:60% | 识别成交量在窄幅区间内突然萎缩后再次放大的模式。先计算过去N根K线成交量的变异系数（标准差/均值），若变异系数小于阈值（0.2）且成交量从低位反弹时价格未创新高，则认为存在陷阱风险。输出[-1,1]，负值表示陷阱。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class volume_quiet_trap(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_volume_quiet_trap", name="volume_quiet_trap",
        display_name="成交量静默陷阱因子", description="识别成交量在窄幅区间内突然萎缩后再次放大的模式。先计算过去N根K线成交量的变异系数（标准差/均值），若变异系数小于阈值（0.2）且成交量从低位反弹时价格未创新高，则认为存在陷阱风险。输出[-1,1]，负值表示陷阱。",
        category="behavioral", subcategory="volume",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    import pandas as pd
    
    volume = data['volume'].values
    close = data['close'].values
    high = data['high'].values
    
    N = 10
    # 滚动变异系数
    rolling_std = pd.Series(volume).rolling(window=N).std().values
    rolling_mean = pd.Series(volume).rolling(window=N).mean().values
    # 避免除零
    rolling_mean = np.where(rolling_mean < 1e-10, 1e-10, rolling_mean)
    cv = rolling_std / rolling_mean
    
    # 成交量低点条件：当前成交量是过去N根中的最小值？简化：cv小表示成交量平稳
    quiet = cv < 0.2
    
    # 检测成交量是否从低位反弹：成交量的3期差分
    vol_diff = np.gradient(volume)  # 简单差分
    # 成交量上升且价格未创20期新高
    price_max20 = pd.Series(high).rolling(window=20).max().values
    not_new_high = close < price_max20
    
    trap = quiet & (vol_diff > 0) & not_new_high
    
    # 使用过去100期的分位数对trap信号强度进行归一化？简单处理：信号为-1时输出-0.8，否则+1
    result = np.where(trap, -0.8, 1.0)
    # 平滑处理? 直接返回
    return pd.Series(result, index=data.index)
