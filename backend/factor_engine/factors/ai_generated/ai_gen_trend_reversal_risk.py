"""AI因子: 趋势反转风险 | 置信:70% | 利用动量与波动率的关系捕捉趋势反转风险，针对profit_drawdown_full和ai_reverse亏损模式。计算近期价格动量（如13周期ROC）与波动率（ATR/close）的比值，当动量快速衰减且波动率放大时指示反转概率高。输出-1表示强烈反转风险（应避免当前方向交易），+1表示趋势稳定。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Trend Reversal Risk(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_trend_reversal_risk", name="Trend Reversal Risk",
        display_name="趋势反转风险", description="利用动量与波动率的关系捕捉趋势反转风险，针对profit_drawdown_full和ai_reverse亏损模式。计算近期价格动量（如13周期ROC）与波动率（ATR/close）的比值，当动量快速衰减且波动率放大时指示反转概率高。输出-1表示强烈反转风险（应避免当前方向交易），+1表示趋势稳定。",
        category="behavioral", subcategory="momentum",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    close = data['close']
    high = data['high']
    low = data['low']
    # 动量
    roc = (close - close.shift(13)) / close.shift(13)
    # 波动率
    tr = np.maximum(high - low, np.maximum(np.abs(high - close.shift(1)), np.abs(low - close.shift(1))))
    atr = tr.rolling(14).mean()
    norm_vol = atr / close
    # 信号：动量变小但波动率变大 -> 反转
    # 使用z-score
    roc_z = (roc - roc.rolling(20).mean()) / (roc.rolling(20).std() + 1e-8)
    vol_z = (norm_vol - norm_vol.rolling(20).mean()) / (norm_vol.rolling(20).std() + 1e-8)
    # 反转信号：当roc_z为负且vol_z为正时为多头反转风险（-1），反之亦然
    reverse_signal = (roc_z * -1 + vol_z) / 2  # 组合
    result = np.clip(reverse_signal, -1, 1)
    return result.fillna(0.0)
