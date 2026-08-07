"""AI因子: 市场噪声因子 | 置信:70% | 通过计算价格变动方向的一致性（利用正负收益率累计差）来量化市场趋势的清晰度。低噪声（高一致性）时趋势延续，高噪声（低一致性）时价格反复震荡。针对'regime=unknown'的混乱市场环境，识别噪声区域避免趋势策略亏损。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Market Noise Level(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_noise", name="Market Noise Level",
        display_name="市场噪声因子", description="通过计算价格变动方向的一致性（利用正负收益率累计差）来量化市场趋势的清晰度。低噪声（高一致性）时趋势延续，高噪声（低一致性）时价格反复震荡。针对'regime=unknown'的混乱市场环境，识别噪声区域避免趋势策略亏损。",
        category="composite", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    # 计算收益率
    ret = data['close'].pct_change()
    # 符号序列
    up = (ret > 0).astype(int)
    down = (ret < 0).astype(int)
    # 累计正负次数差（窗口20）
    window = 20
    cum_up = up.rolling(window).sum()
    cum_down = down.rolling(window).sum()
    # 方向一致性指标 [-1,1]  1表示全部向上，-1全部向下
    direction = (cum_up - cum_down) / (cum_up + cum_down + 1e-10)
    # 噪声：方向一致性绝对值低代表噪声高。我们取绝对值然后取反
    noise = 1 - abs(direction)
    # 平滑并填充
    factor = noise.fillna(0.5) * 2 - 1  # 映射到[-1,1]，0.5对应0
    return factor
