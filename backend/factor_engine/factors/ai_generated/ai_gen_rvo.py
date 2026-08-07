"""AI因子: 波动状态偏移因子 | 置信:55% | 通过比较近期波动率与长期波动率，识别市场是否处于异常低波动（不明模式）或高波动（趋势反转）状态。因子为正表示高波动（可能趋势延续），负表示低波动（不明状态），接近0为正常。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Regime_Volatility_Offset(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_rvo", name="Regime_Volatility_Offset",
        display_name="波动状态偏移因子", description="通过比较近期波动率与长期波动率，识别市场是否处于异常低波动（不明模式）或高波动（趋势反转）状态。因子为正表示高波动（可能趋势延续），负表示低波动（不明状态），接近0为正常。",
        category="technical", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    close = data['close'].values
    log_ret = np.log(close[1:] / close[:-1])
    # 近期波动率（10日）
    short_window = 10
    long_window = 50
    short_vol = pd.Series(log_ret).rolling(short_window).std().fillna(log_ret.std())
    long_vol = pd.Series(log_ret).rolling(long_window).std().fillna(log_ret.std())
    # 波动率比值，并取对数
    vol_ratio = short_vol / (long_vol + 1e-10)
    # 标准化到[-1,1]：使用sigmoid或clip
    # 通常比率在0.5~2之间，映射
    ratio_norm = np.clip((vol_ratio - 0.5) / 1.5, -1, 1)  # 当ratio=0.5时-1，=2时1，=1时0
    result = ratio_norm
    # 填充NaN并返回
    # 由于短周期波动可能缺失，向前填充
    result = result.fillna(method='ffill').fillna(0)
    return pd.Series(result, index=data.index).clip(-1,1)
