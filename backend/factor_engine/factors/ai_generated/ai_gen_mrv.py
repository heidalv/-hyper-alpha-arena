"""AI因子: 均值回归波动修正 | 置信:65% | 基于短期价格动量与波动率比值，识别微小盈利后反转风险。当短期动量弱且波动率高时给出负信号，避免master_running_close_tiny模式"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Mean Reversion Volatility(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_mrv", name="Mean Reversion Volatility",
        display_name="均值回归波动修正", description="基于短期价格动量与波动率比值，识别微小盈利后反转风险。当短期动量弱且波动率高时给出负信号，避免master_running_close_tiny模式",
        category="technical", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    close = data['close']
    high = data['high']
    low = data['low']
    volume = data['volume']
    # 短期动量：过去3日收益率
    ret3 = close.pct_change(3)
    # 波动率：过去5日ATR相对收盘价
    atr = (high - low).rolling(5).mean() / close
    # 成交量变化率
    vol_ratio = volume / volume.rolling(5).mean()
    # 组合：动量弱且波动大时负值
    signal = -ret3 * atr * vol_ratio.clip(0, 2)
    # 归一化到[-1,1]
    result = signal / (signal.abs().mean() + 1e-8)
    return result.clip(-1, 1)
