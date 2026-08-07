"""AI因子: 波动率偏离 | 置信:60% | 衡量当前波动率与历史均值偏离程度，异常低或高波动往往对应未知市场状态，导致亏损。通过短期波动率与长期波动率之比，并取负向映射，值越接近-1越应避免开多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Deviation(BaseFactor):
    """衡量当前波动率与历史均值偏离程度，异常低或高波动往往对应未知市场状态，导致亏损。通过短期波动率与长期波动率之比，并取负向映射，值越接近-1越应避免开多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_deviation",
            name="Volatility Deviation",
            display_name="波动率偏离",
            description="衡量当前波动率与历史均值偏离程度，异常低或高波动往往对应未知市场状态，导致亏损。通过短期波动率与长期波动率之比，并取负向映射，值越接近-1越应避免开多。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: DataFrame with columns open, high, low, close, volume
        import numpy as np
        import pandas as pd
        # 计算20日收益率标准差（波动）
        returns = data['close'].pct_change()
        short_term = returns.rolling(10).std()
        long_term = returns.rolling(60).std()
        # 避免除零
        ratio = short_term / (long_term + 1e-10)
        # 当ratio偏离1时（过大或过小）为异常，映射到[-1,0]表示风险
        # 使用高斯核：exp(-((ratio-1)^2)/0.5) 得到0~1，再取负，并调整到[-1,0]
        # 使得正常比值时接近0，异常时趋近-1
        anomaly = np.exp(-((ratio - 1)**2) / 0.5)
        result = -anomaly  # 范围[-1,0]
        # 处理NaN前向填充
        result = result.fillna(method='ffill').fillna(0)
        return result.clip(-1, 1)
