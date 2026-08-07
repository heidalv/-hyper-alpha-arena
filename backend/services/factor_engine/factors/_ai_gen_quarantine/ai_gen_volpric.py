"""AI因子: 波动率-价格背离 | 置信:60% | 计算近期价格变化与波动率的背离程度。当价格出现较大波动但方向与近期趋势相反时，容易触发止损或超时亏损。本因子通过比较过去N根K线的归一化波动率与价格动量，输出反向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Price_Divergence(BaseFactor):
    """计算近期价格变化与波动率的背离程度。当价格出现较大波动但方向与近期趋势相反时，容易触发止损或超时亏损。本因子通过比较过去N根K线的归一化波动率与价格动量，输出反向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volpric",
            name="Volatility_Price_Divergence",
            display_name="波动率-价格背离",
            description="计算近期价格变化与波动率的背离程度。当价格出现较大波动但方向与近期趋势相反时，容易触发止损或超时亏损。本因子通过比较过去N根K线的归一化波动率与价格动量，输出反向信号。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        n = 20
        high = data['high']
        low = data['low']
        close = data['close']
        # 日内波动率: (high-low)/close
        vol = (high - low) / close
        # 价格动量: 过去n期收益率
        ret = close.pct_change(n)
        # 滚动波动率标准差
        vol_std = vol.rolling(n).std()
        # 滚动动量标准差
        ret_std = ret.rolling(n).std()
        # 标准化
        vol_z = (vol - vol.rolling(n).mean()) / vol_std.clip(lower=1e-8)
        ret_z = (ret - ret.rolling(n).mean()) / ret_std.clip(lower=1e-8)
        # 背离: 当价格动量负且波动率正时，发出负信号（看空）；反之亦然
        divergence = -vol_z * ret_z
        # 映射到[-1,1]
        result = divergence.clip(-3, 3) / 3.0
        return result.fillna(0)
