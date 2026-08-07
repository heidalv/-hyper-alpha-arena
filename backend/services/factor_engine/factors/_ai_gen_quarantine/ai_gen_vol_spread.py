"""AI因子: 波动率离差 | 置信:60% | 基于近期日内振幅的异常变化，识别市场状态突变。当波动率偏离移动平均超过阈值时，预示不稳定行情，容易导致方向性亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilitySpread(BaseFactor):
    """基于近期日内振幅的异常变化，识别市场状态突变。当波动率偏离移动平均超过阈值时，预示不稳定行情，容易导致方向性亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_spread",
            name="VolatilitySpread",
            display_name="波动率离差",
            description="基于近期日内振幅的异常变化，识别市场状态突变。当波动率偏离移动平均超过阈值时，预示不稳定行情，容易导致方向性亏损。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算日内振幅
        h = data['high']
        l = data['low']
        c = data['close']
        o = data['open']
        # 真实振幅（考虑跳空）
        prev_c = c.shift(1)
        tr = np.maximum(h - l, np.abs(h - prev_c), np.abs(l - prev_c))
        # 滚动均值和标准差（20期）
        tr_ma = tr.rolling(20).mean()
        tr_std = tr.rolling(20).std()
        # Z-score，然后截断到[-3,3]并缩放到[-1,1]
        z = (tr - tr_ma) / (tr_std + 1e-10)
        z_clipped = np.clip(z, -3, 3)
        result = -z_clipped / 3.0  # 负号：高波动给出负值（警告）
        return result
