"""AI因子: 未知状态风险 | 置信:60% | 综合趋势强度与波动率异常，识别类似于'regime=unknown'的高风险状态。计算短期趋势强度（如线性回归斜率）与长期波动率的比值，当趋势模糊且波动率扩大时发出反向信号。该因子旨在避免在不确定市场中追涨杀跌，尤其是'unknown' regime下的亏损。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Unknown Regime Risk(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_unknown_regime_risk", name="Unknown Regime Risk",
        display_name="未知状态风险", description="综合趋势强度与波动率异常，识别类似于'regime=unknown'的高风险状态。计算短期趋势强度（如线性回归斜率）与长期波动率的比值，当趋势模糊且波动率扩大时发出反向信号。该因子旨在避免在不确定市场中追涨杀跌，尤其是'unknown' regime下的亏损。",
        category="composite", subcategory="trend",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    # data: pd.DataFrame
    import numpy as np
    from sklearn.linear_model import LinearRegression
    short_window = 5
    long_window = 20
    # 计算短期线性斜率作为趋势强度
    def slope(series):
        x = np.arange(len(series))
        if len(series) < 2:
            return 0
        lr = LinearRegression().fit(x.reshape(-1,1), series.values)
        return lr.coef_[0]
    # 滚动斜率
    short_slope = data['close'].rolling(short_window).apply(lambda s: slope(s), raw=False)
    # 计算长期波动率（标准差）
    long_std = data['close'].rolling(long_window).std()
    # 趋势强度归一化
    trend_strength = short_slope / (long_std + 1e-10)
    # 当趋势强度绝对值小（趋势模糊）且长期波动率近期增大时，认为是高风险
    vol_change = long_std / long_std.shift(5).fillna(method='ffill')
    unknown_condition = (np.abs(trend_strength) < 0.05) & (vol_change > 1.2)
    # 信号：模糊且波动增大时，反转之前的微趋势方向
    # 使用过去N期的平均方向
    prior_direction = np.sign(data['close'].diff(3)).rolling(5).mean()
    signal = np.where(unknown_condition, -prior_direction, 0)
    result = pd.Series(signal, index=data.index).fillna(0)
    return result.clip(-1, 1)
