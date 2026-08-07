"""AI因子: 微观价量背离向量 | 置信:55% | 检测短时间窗口内价格与成交量的背离程度。若价格小幅上涨但成交量显著减少，预示上涨乏力；反之亦然。计算价格变动符号与成交量变动的相关系数，取负号后归一化。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Micro Divergence Vector(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_mdv", name="Micro Divergence Vector",
        display_name="微观价量背离向量", description="检测短时间窗口内价格与成交量的背离程度。若价格小幅上涨但成交量显著减少，预示上涨乏力；反之亦然。计算价格变动符号与成交量变动的相关系数，取负号后归一化。",
        category="behavioral", subcategory="contrarian",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data: pd.DataFrame) -> pd.Series:
    import numpy as np
    n = 5
    close_diff = data['close'].diff()
    vol_diff = data['volume'].diff()
    corr = close_diff.rolling(n).corr(vol_diff).fillna(0)
    raw = -corr
    return np.clip(raw, -1, 1)
