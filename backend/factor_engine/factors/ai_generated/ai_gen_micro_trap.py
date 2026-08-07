"""AI因子: 微小移动陷阱因子 | 置信:65% | 识别价格在窄幅区间内微小波动且短期动量与长期趋势相反的情形，这种状态常导致反向开仓亏损。计算近期收盘价变化率的标准差与ATR的比值，当比值低于阈值且短期方向与长期方向相反时输出负值，否则输出正。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Micro Movement Trap Indicator(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_micro_trap", name="Micro Movement Trap Indicator",
        display_name="微小移动陷阱因子", description="识别价格在窄幅区间内微小波动且短期动量与长期趋势相反的情形，这种状态常导致反向开仓亏损。计算近期收盘价变化率的标准差与ATR的比值，当比值低于阈值且短期方向与长期方向相反时输出负值，否则输出正。",
        category="technical", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    df = data.copy()
    # 计算ATR (14日)
    tr = pd.concat([(df['high'] - df['low']).abs(),
                    (df['high'] - df['close'].shift()).abs(),
                    (df['low'] - df['close'].shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    # 短期收盘价变化的标准差 (5日)
    short_roc = df['close'].pct_change(5)
    short_std = short_roc.rolling(5).std()
    # 归一化比值: small_ratio = short_std / (atr/close.mean() + 1e-8) 近似
    close_avg = df['close'].rolling(20).mean()
    ratio = short_std / (atr / (close_avg + 1e-8) + 1e-8)
    # 短期方向 (5日ROC)
    roc5 = df['close'].pct_change(5)
    # 长期方向 (20日ROC)
    roc20 = df['close'].pct_change(20)
    # 方向相反信号
    opposite = (roc5 * roc20 < 0).astype(float) * (-1)
    # 阀值: ratio < 0.3 认为微小移动
    threshold = 0.3
    micro = (ratio < threshold).astype(float)
    result = micro * opposite
    # 平滑并映射到[-1,1]
    result = result.rolling(3, min_periods=1).mean()
    return result.fillna(0).clip(-1, 1)
