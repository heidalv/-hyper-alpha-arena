"""AI因子: 利润回撤风险因子 | 置信:65% | 基于最近N根K线的最大回撤与当前价格的波动率（ATR）比值，衡量价格在高位时潜在的回撤风险。当回撤风险低时因子接近+1（安全），风险高时接近-1（警惕回撤）。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class DrawdownRisk(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_dd_risk", name="DrawdownRisk",
        display_name="利润回撤风险因子", description="基于最近N根K线的最大回撤与当前价格的波动率（ATR）比值，衡量价格在高位时潜在的回撤风险。当回撤风险低时因子接近+1（安全），风险高时接近-1（警惕回撤）。",
        category="technical", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    close = data['close']
    high = data['high']
    low = data['low']
    # 计算20周期滚动最大回撤（从最高点到当前低点的百分比）
    rolling_high = high.rolling(20).max()
    drawdown = (rolling_high - low) / (rolling_high + 1e-10)
    # 计算20周期ATR
    tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
    atr = tr.rolling(20).mean()
    # 相对波动调整：drawdown / (atr/close) 表示回撤相对于平均波动幅度
    risk_ratio = drawdown / (atr / (close + 1e-10) + 1e-10)
    # 映射到[-1,1]：当risk_ratio小（<0.5）时接近1，大（>2）时接近-1
    score = 2.0 / (1.0 + np.exp(risk_ratio * 2.0 - 2.0)) - 1.0
    return score.clip(-1, 1)
