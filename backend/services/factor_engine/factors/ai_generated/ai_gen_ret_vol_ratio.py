"""AI因子: 收益波动比 | 置信:45% | 亏损模式显示在regime=unknown下，多空均出现超时或止损，且亏损幅度与持仓时间相关，说明趋势信号在低波动或高波动环境下失效。本因子计算20日累计收益与20日波动率的比值，识别收益与波动不匹配（即收益无法覆盖波动成本）的区间，此类区间容易产生超时亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReturnVolatilityRatio(BaseFactor):
    """亏损模式显示在regime=unknown下，多空均出现超时或止损，且亏损幅度与持仓时间相关，说明趋势信号在低波动或高波动环境下失效。本因子计算20日累计收益与20日波动率的比值，识别收益与波动不匹配（即收益无法覆盖波动成本）的区间，此类区间容易产生超时亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ret_vol_ratio",
            name="Return-Volatility Ratio",
            display_name="收益波动比",
            description="亏损模式显示在regime=unknown下，多空均出现超时或止损，且亏损幅度与持仓时间相关，说明趋势信号在低波动或高波动环境下失效。本因子计算20日累计收益与20日波动率的比值，识别收益与波动不匹配（即收益无法覆盖波动成本）的区间，此类区间容易产生超时亏损。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret20 = data['close'].pct_change(20)
        vol20 = data['close'].pct_change().rolling(20).std()
        ratio = ret20 / (vol20 * 20 ** 0.5 + 1e-9)
        result = (ratio * 2).clip(-1, 1)
        return result
