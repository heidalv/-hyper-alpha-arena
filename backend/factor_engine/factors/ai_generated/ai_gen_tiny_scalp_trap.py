"""AI因子: 微小波动陷阱 | 置信:65% | 检测价格在窄幅区间内快速波动，常见于master_running_close_tiny亏损模式。通过计算近数根K线的平均真实波幅(ATR)与价格波动率的比率，识别流动性陷阱或假突破风险。值接近+1表示波动极小且方向不明，建议避免交易；-1表示正常波动可参与。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Micro Volatility Trapping(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_tiny_scalp_trap", name="Micro Volatility Trapping",
        display_name="微小波动陷阱", description="检测价格在窄幅区间内快速波动，常见于master_running_close_tiny亏损模式。通过计算近数根K线的平均真实波幅(ATR)与价格波动率的比率，识别流动性陷阱或假突破风险。值接近+1表示波动极小且方向不明，建议避免交易；-1表示正常波动可参与。",
        category="technical", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    # 计算ATR
    high = data['high']
    low = data['low']
    close = data['close']
    tr = np.maximum(high - low, np.maximum(np.abs(high - close.shift(1)), np.abs(low - close.shift(1))))
    atr = tr.rolling(14).mean()
    # 价格变化率（1分钟窗口，假设为分钟级别）
    price_change = (close - close.shift(5)).abs() / close.shift(5)
    # 比率：ATR相对价格变化
    ratio = atr / (price_change + 1e-8)
    # 标准化到[-1,1]，高比率表示波动大但价格变化小（陷阱），低比率表示正常
    # 使用分位数映射
    mean = ratio.rolling(20).mean()
    std = ratio.rolling(20).std()
    normalized = (ratio - mean) / (std + 1e-8)
    result = np.clip(normalized, -3, 3) / 3 * -1  # 反转：高ratio -> -1（避免）
    return result.fillna(0.0)
