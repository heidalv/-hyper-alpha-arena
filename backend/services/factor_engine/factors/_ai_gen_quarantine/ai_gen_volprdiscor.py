"""AI因子: 量价背离离散度 | 置信:60% | 在亏损样本中，多次出现在市场状态未知时的止损，量价关系紊乱往往是信号。该因子计算短期量价相关性（例如过去10根K线）的异常程度，若相关性接近0或负值且偏离历史均值超过一个标准差，则输出负值（预示风险）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDiscorrelation(BaseFactor):
    """在亏损样本中，多次出现在市场状态未知时的止损，量价关系紊乱往往是信号。该因子计算短期量价相关性（例如过去10根K线）的异常程度，若相关性接近0或负值且偏离历史均值超过一个标准差，则输出负值（预示风险）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volprdiscor",
            name="volume_price_discorrelation",
            display_name="量价背离离散度",
            description="在亏损样本中，多次出现在市场状态未知时的止损，量价关系紊乱往往是信号。该因子计算短期量价相关性（例如过去10根K线）的异常程度，若相关性接近0或负值且偏离历史均值超过一个标准差，则输出负值（预示风险）。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算对数收益率和成交量变化率
        ret = np.log(data['close'] / data['close'].shift(1))
        vol_change = np.log(data['volume'] / data['volume'].shift(1))
        # 滚动10期相关系数
        def rolling_corr(x, y):
            return x.rolling(10).corr(y)
        corr = rolling_corr(ret, vol_change)
        # 计算过去50期相关系数的均值和标准差
        corr_mean = corr.rolling(50).mean()
        corr_std = corr.rolling(50).std()
        # 计算当前相关性相对于均值的偏差Z-score
        z = (corr - corr_mean) / corr_std
        # 当相关系数绝对值小于0.2且Z-score小于-1时，认为量价背离严重
        # 映射到[-1,1]：背离越严重越接近-1，正常时接近0或正
        factor = np.where(
            (np.abs(corr) < 0.2) & (z < -1),
            -1.0,
            np.where(
                (np.abs(corr) > 0.5) & (z > 1),
                1.0,
                0.0
            )
        )
        return pd.Series(factor, index=data.index)
