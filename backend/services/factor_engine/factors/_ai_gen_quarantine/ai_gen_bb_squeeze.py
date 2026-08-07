"""AI因子: 布林带挤压 | 置信:50% | 衡量价格波动是否处于收缩期（低波动），当布林带宽度处于历史低位时，市场可能进入无趋势状态（regime=unknown）。因子值为负表示挤压（震荡），正表示扩张（趋势）。使用带宽百分比变化并映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Bollinger_Band_Squeeze(BaseFactor):
    """衡量价格波动是否处于收缩期（低波动），当布林带宽度处于历史低位时，市场可能进入无趋势状态（regime=unknown）。因子值为负表示挤压（震荡），正表示扩张（趋势）。使用带宽百分比变化并映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bb_squeeze",
            name="Bollinger Band Squeeze",
            display_name="布林带挤压",
            description="衡量价格波动是否处于收缩期（低波动），当布林带宽度处于历史低位时，市场可能进入无趋势状态（regime=unknown）。因子值为负表示挤压（震荡），正表示扩张（趋势）。使用带宽百分比变化并映射到[-1,1]。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        window = 20
        num_std = 2
        sma = close.rolling(window=window).mean()
        std = close.rolling(window=window).std()
        upper = sma + num_std * std
        lower = sma - num_std * std
        bandwidth = (upper - lower) / sma
        # 计算带宽的百分位排名（过去100期）
        rank = bandwidth.rolling(window=100).apply(lambda x: (x[-1] - x.min()) / (x.max() - x.min() + 1e-10), raw=False)
        # 映射：0~0.2挤压，0.2~0.8中性，0.8~1扩张
        result = (rank - 0.5) * 2  # 变为-1 ~ 1
        return result.fillna(0).clip(-1, 1)
