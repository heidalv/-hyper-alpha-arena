"""AI因子: 超时止损波动率守卫 | 置信:70% | 针对max_hold_timeout亏损模式：当价格在持仓期内横盘震荡（低效率波动）且波动率处于高位时，趋势策略容易超时止损。该因子通过近期收益效率（收盘价净变动/总路径长度）与波动率扩张的比值，识别低效率高波动环境，值越高越接近-1（做空波动率或规避趋势）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TimeoutRegimeVolatilityGuard(BaseFactor):
    """针对max_hold_timeout亏损模式：当价格在持仓期内横盘震荡（低效率波动）且波动率处于高位时，趋势策略容易超时止损。该因子通过近期收益效率（收盘价净变动/总路径长度）与波动率扩张的比值，识别低效率高波动环境，值越高越接近-1（做空波动率或规避趋势）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timeout_rv",
            name="Timeout_Regime_Volatility_Guard",
            display_name="超时止损波动率守卫",
            description="针对max_hold_timeout亏损模式：当价格在持仓期内横盘震荡（低效率波动）且波动率处于高位时，趋势策略容易超时止损。该因子通过近期收益效率（收盘价净变动/总路径长度）与波动率扩张的比值，识别低效率高波动环境，值越高越接近-1（做空波动率或规避趋势）。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(20)
        path = (data['close'] / data['close'].shift(1) - 1).abs().rolling(20).sum()
        efficiency = ret / (path + 1e-9)
        vol = data['close'].pct_change().rolling(20).std()
        result = (efficiency - vol).clip(-1, 1)
        return result
