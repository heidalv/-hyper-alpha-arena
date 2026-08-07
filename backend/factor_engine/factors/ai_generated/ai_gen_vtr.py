"""AI因子: 波动趋势比 | 置信:60% | 近期波动率（ATR10）与价格变化绝对值（10日回报绝对值）的比值。比值高表示波动大但方向不明（噪声高），输出接近-1；比值低表示方向明确，输出接近+1。用于识别高噪声低辨识度行情。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volatility Trend Ratio(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_vtr", name="Volatility Trend Ratio",
        display_name="波动趋势比", description="近期波动率（ATR10）与价格变化绝对值（10日回报绝对值）的比值。比值高表示波动大但方向不明（噪声高），输出接近-1；比值低表示方向明确，输出接近+1。用于识别高噪声低辨识度行情。",
        category="technical", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    high = data['high']
    low = data['low']
    close = data['close']
    # ATR 10
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr10 = tr.rolling(10).mean()
    # 10日回报绝对值
    ret10 = (close / close.shift(10) - 1).abs()
    # 比值，使用rolling最小化异常
    ratio = atr10 / (ret10 * close + 1e-10)  # 分母为价格变化绝对值近似
    # 实际更准确：价格变化绝对值 = (close - close.shift(10)).abs()
    price_change = (close - close.shift(10)).abs()
    ratio = atr10 / (price_change + 1e-10)
    # 标准化到[-1,1]，使用log变换
    log_ratio = np.log(ratio + 1e-10)
    # 用滚动中位数和标准差归一化
    med = log_ratio.rolling(50).median()
    std = log_ratio.rolling(50).std()
    normalized = (log_ratio - med) / (std + 1e-10)
    # 映射到[-1,1]，截断
    result = -np.tanh(normalized)  # 高比值转负
    result = result.clip(-1, 1)
    result = result.fillna(0.0)
    return result
