"""AI因子: 波动率收缩 | 置信:60% | 测量近期波动率相对于历史波动率的收缩程度。当波动率极度收缩时，市场往往处于盘整且方向不明，此时频繁交易容易因假突破而亏损。因子输出负值以提示规避。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class VolatilityContraction(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_volcon", name="VolatilityContraction",
        display_name="波动率收缩", description="测量近期波动率相对于历史波动率的收缩程度。当波动率极度收缩时，市场往往处于盘整且方向不明，此时频繁交易容易因假突破而亏损。因子输出负值以提示规避。",
        category="technical", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data: pd.DataFrame) -> pd.Series:
    high, low, close = data['high'], data['low'], data['close']
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    # 计算当前ATR相对于过去100期ATR中位数的偏离
    atr_med = atr.rolling(100).median()
    ratio = atr / atr_med - 1
    # 负值表示收缩，正值表示扩张。用对称变换映射到[-1,1]
    result = -np.tanh(ratio * 3)  # 收缩时ratio <0 => -tanh(负) => 正? 我们需要负信号表示收缩危险，所以用负号调整
    # 更直观：收缩时ratio负，我们希望输出负，所以 result = -tanh(ratio*3) 当ratio负时 -tanh负=正，不对；改用 tanh(-ratio*3) 则收缩时ratio负 => -ratio正 => tanh正，再取负？
    # 正确：收缩时ratio负，我们希望输出负（警告）。使用 result = tanh(ratio*3) 则收缩时tanh负，输出负。
    result = np.tanh(ratio * 3)
    return result.fillna(0)
