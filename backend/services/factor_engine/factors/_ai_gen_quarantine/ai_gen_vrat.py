"""AI因子: 波动率比率异常 | 置信:60% | 计算近期ATR与长期ATR的比值，当比值偏离1个标准差时发出信号，用于识别regime=unknown的异常波动环境。比值过高或过低均可能导致多头亏损，因子输出负值表示危险（避免做多）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Ratio_Anomaly(BaseFactor):
    """计算近期ATR与长期ATR的比值，当比值偏离1个标准差时发出信号，用于识别regime=unknown的异常波动环境。比值过高或过低均可能导致多头亏损，因子输出负值表示危险（避免做多）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vrat",
            name="Volatility Ratio Anomaly",
            display_name="波动率比率异常",
            description="计算近期ATR与长期ATR的比值，当比值偏离1个标准差时发出信号，用于识别regime=unknown的异常波动环境。比值过高或过低均可能导致多头亏损，因子输出负值表示危险（避免做多）。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算ATR
        high = data['high']
        low = data['low']
        close = data['close']
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr_short = tr.rolling(5).mean()
        atr_long = tr.rolling(20).mean()
        ratio = atr_short / atr_long
        # 标准化
        mean_ratio = ratio.rolling(60).mean()
        std_ratio = ratio.rolling(60).std()
        zscore = (ratio - mean_ratio) / std_ratio
        # 当zscore绝对值大于1时，输出负值（危险），否则接近0
        result = -np.clip(zscore.abs() - 1, 0, 1)
        return result.fillna(0.0)
