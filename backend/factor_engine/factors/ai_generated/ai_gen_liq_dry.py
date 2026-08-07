"""AI因子: 流动性枯竭指标 | 置信:50% | 基于Amihud非流动性指标（取对数），通过滚动窗口标准化后判断异常。当非流动性突然飙升（超过均值+2倍标准差），表明流动性枯竭，易导致滑点和异常亏损，因子输出-1；否则+1。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Liquidity Dry-up Indicator(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_liq_dry", name="Liquidity Dry-up Indicator",
        display_name="流动性枯竭指标", description="基于Amihud非流动性指标（取对数），通过滚动窗口标准化后判断异常。当非流动性突然飙升（超过均值+2倍标准差），表明流动性枯竭，易导致滑点和异常亏损，因子输出-1；否则+1。",
        category="composite", subcategory="volume",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    close = data['close']
    volume = data['volume']
    # Amihud: |return| / volume (避免除零)
    ret = close.pct_change().abs()
    volume_adj = volume.replace(0, np.nan).ffill()
    illiq = ret / volume_adj
    # 滚动均值和标准差
    mean_ill = illiq.rolling(30).mean()
    std_ill = illiq.rolling(30).std()
    z = (illiq - mean_ill) / std_ill
    # 当z大于2时视为流动性枯竭
    result = pd.Series(np.where(z > 2, -1, 1), index=data.index)
    # 前30天无数据时设为1
    result = result.fillna(1)
    return result
