"""AI因子: 滚动回撤风险评分 | 置信:55% | 基于当前价格相对于近期高点回撤幅度和持仓时间长度（用窗口内最大回撤持续时间代理），评估持有头寸可能遭遇的大幅回撤风险。当回撤超过阈值且持续时间较长时，信号接近+1（高回撤风险），反之接近-1。防止类似"profit_drawdown_full"和"sl"亏损。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Rolling Drawdown Risk Score(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_drawdown_risk", name="Rolling Drawdown Risk Score",
        display_name="滚动回撤风险评分", description="基于当前价格相对于近期高点回撤幅度和持仓时间长度（用窗口内最大回撤持续时间代理），评估持有头寸可能遭遇的大幅回撤风险。当回撤超过阈值且持续时间较长时，信号接近+1（高回撤风险），反之接近-1。防止类似"profit_drawdown_full"和"sl"亏损。",
        category="behavioral", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    # data: DataFrame with columns ['open','high','low','close','volume']
    import pandas as pd
    import numpy as np
    close = data['close']
    
    # 计算滚动累积最大值
    rolling_max = close.rolling(20, min_periods=1).max()
    # 计算当前回撤比例
    drawdown = (close - rolling_max) / rolling_max
    
    # 计算回撤持续时间（连续处于回撤状态的期数）
    # 回撤状态定义为drawdown < -0.01 (1%回撤)
    in_drawdown = (drawdown < -0.01).astype(int)
    # 使用累积和计算持续时间
    drawdown_duration = in_drawdown.groupby((in_drawdown != in_drawdown.shift()).cumsum()).cumsum()
    
    # 风险评分 = 当前回撤幅度 * 持续时间正则化
    risk = np.abs(drawdown) * drawdown_duration
    # 映射到[-1,1]：风险高时接近+1，低时接近-1
    # 使用clip和线性映射
    risk_scaled = 2 * (risk - risk.rolling(60).min()) / (risk.rolling(60).max() - risk.rolling(60).min() + 1e-8) - 1
    return risk_scaled.fillna(0)
