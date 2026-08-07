"""AI因子: 振幅成交量因子 | 置信:65% | 结合价格振幅与成交量，高振幅低成交量表示假突破或流动性不足，容易导致亏损平仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class AmplitudeVolumeFactor(BaseFactor):
    """结合价格振幅与成交量，高振幅低成交量表示假突破或流动性不足，容易导致亏损平仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ampvol",
            name="AmplitudeVolumeFactor",
            display_name="振幅成交量因子",
            description="结合价格振幅与成交量，高振幅低成交量表示假突破或流动性不足，容易导致亏损平仓。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 振幅：最高最低价差相对于收盘价
        amp = (data['high'] - data['low']) / data['close']
        # 成交量相对均值
        vol = data['volume'] / data['volume'].rolling(20).mean()
        # 构建因子：振幅大且成交量小时为负值
        factor = -amp * (1 / (vol + 1e-10))
        # 标准化
        mean = factor.rolling(60).mean()
        std = factor.rolling(60).std()
        z = (factor - mean) / (std + 1e-10)
        result = pd.Series(np.tanh(z * 0.5), index=data.index)
        return result.clip(-1, 1)
