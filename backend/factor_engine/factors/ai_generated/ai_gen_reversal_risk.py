"""AI因子: 短期反转风险 | 置信:65% | 检测价格处于近期低位且波动率急剧放大，可能预示空头回补反弹。计算过去N根K线内最低价与当前收盘价的相对距离，乘以最近波动率变化率，最后归一化到[-1,1]。正值表示反转风险高（应避免做空），负值表示趋势延续。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Short-term Reversal Risk(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_reversal_risk", name="Short-term Reversal Risk",
        display_name="短期反转风险", description="检测价格处于近期低位且波动率急剧放大，可能预示空头回补反弹。计算过去N根K线内最低价与当前收盘价的相对距离，乘以最近波动率变化率，最后归一化到[-1,1]。正值表示反转风险高（应避免做空），负值表示趋势延续。",
        category="behavioral", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    close = data['close']
    low = data['low']
    high = data['high']
    n = 14
    # 近期最低价
    rolling_min = low.rolling(window=n).min()
    # 价格相对位置：远离近期最低的程度 (0~1)
    dist_from_low = (close - rolling_min) / (close + 1e-10)
    # 波动率变化：当前ATR与过去N天均值ATR的比值
    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(window=n).mean()
    atr_change = (tr - atr) / (atr + 1e-10)
    # 组合：价格接近低位且波动率放大 => 反转风险高
    raw = (1 - dist_from_low) * atr_change
    # 归一化到[-1,1] (用3倍标准差截断)
    std = raw.rolling(window=200).std()
    result = raw / (3 * std + 1e-10)
    result = result.clip(-1, 1)
    return result
