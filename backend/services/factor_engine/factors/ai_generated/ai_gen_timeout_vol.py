"""AI因子: 超时亏损波动率状态 | 置信:70% | 捕捉max_hold_timeout亏损模式：当价格在持仓期内波动率收缩（窄幅震荡）导致无法触发止盈止损而超时离场。因子衡量近期波动率相对于长期波动率的收缩程度，值越高表示波动率越收缩，越容易超时亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TimeoutVolatilityRegime(BaseFactor):
    """捕捉max_hold_timeout亏损模式：当价格在持仓期内波动率收缩（窄幅震荡）导致无法触发止盈止损而超时离场。因子衡量近期波动率相对于长期波动率的收缩程度，值越高表示波动率越收缩，越容易超时亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timeout_vol",
            name="Timeout_Volatility_Regime",
            display_name="超时亏损波动率状态",
            description="捕捉max_hold_timeout亏损模式：当价格在持仓期内波动率收缩（窄幅震荡）导致无法触发止盈止损而超时离场。因子衡量近期波动率相对于长期波动率的收缩程度，值越高表示波动率越收缩，越容易超时亏损。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_vol = data['close'].pct_change().rolling(5).std()
        long_vol = data['close'].pct_change().rolling(30).std()
        vol_ratio = short_vol / (long_vol + 1e-9)
        result = (1 - vol_ratio).clip(0, 1) * 2 - 1
        return result
