"""AI因子: 乖离回归 | 置信:65% | 计算收盘价相对于20日均线的乖离率，并考虑波动率调整。当乖离率超过正负1.5倍标准差时，预示价格将回归，给出反向信号。使用滚动20周期计算均值和标准差，然后归一化到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class BiasRegression(BaseFactor):
    """计算收盘价相对于20日均线的乖离率，并考虑波动率调整。当乖离率超过正负1.5倍标准差时，预示价格将回归，给出反向信号。使用滚动20周期计算均值和标准差，然后归一化到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bias",
            name="BiasRegression",
            display_name="乖离回归",
            description="计算收盘价相对于20日均线的乖离率，并考虑波动率调整。当乖离率超过正负1.5倍标准差时，预示价格将回归，给出反向信号。使用滚动20周期计算均值和标准差，然后归一化到[-1,1]。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        period = 20
        ma = data['close'].rolling(period, min_periods=1).mean()
        std = data['close'].rolling(period, min_periods=1).std(ddof=0).replace(0, np.nan)
        bias = (data['close'] - ma) / std
        # 对bias进行限幅，超过3倍标准差则截断
        bias = bias.clip(-3, 3)
        result = -bias / 3.0  # 负相关：正乖离给出负信号，反之
        # 填补NaN为0
        result = result.fillna(0.0)
        return result.clip(-1, 1)
