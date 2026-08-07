"""AI因子: 趋势一致性指数 | 置信:65% | 衡量短期(5日)与中期(20日)价格移动平均的方向一致性。当两者同向时赋予较强信号，反向时信号衰减，避免在均线缠绕的不确定状态下交易。根据亏损模式，做空在趋势不明时易亏损，本因子让信号趋于中性。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Trend Consistency Index(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_trend_consist", name="Trend Consistency Index",
        display_name="趋势一致性指数", description="衡量短期(5日)与中期(20日)价格移动平均的方向一致性。当两者同向时赋予较强信号，反向时信号衰减，避免在均线缠绕的不确定状态下交易。根据亏损模式，做空在趋势不明时易亏损，本因子让信号趋于中性。",
        category="technical", subcategory="trend",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    close = data['close']
    # 短期与中期均线
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    # 计算斜率（价格变化率）
    slope5 = (ma5 - ma5.shift(5)) / ma5.shift(5)
    slope20 = (ma20 - ma20.shift(20)) / ma20.shift(20)
    # 一致性：cosine相似度归一化到[-1,1]（符号积）
    consistency = np.sign(slope5) * np.sign(slope20)
    # 乘以平均斜率强度进行调节
    strength = (abs(slope5) + abs(slope20)) / 2
    # 使用tanh限制强度并合成
    raw = consistency * np.tanh(10 * strength)
    result = raw.rolling(3).mean().fillna(0)
    return result
