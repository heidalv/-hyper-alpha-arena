"""AI因子: 超时亏损波动收益 | 置信:60% | 捕捉因持仓超时导致亏损的模式：当价格波动率上升但收益停滞时，容易触发超时平仓。因子为负波动率调整后的短期收益，高值表示波动大但收益差，预示超时亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Timeoutvolret(BaseFactor):
    """捕捉因持仓超时导致亏损的模式：当价格波动率上升但收益停滞时，容易触发超时平仓。因子为负波动率调整后的短期收益，高值表示波动大但收益差，预示超时亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timeout_vol_ret",
            name="TimeoutVolRet",
            display_name="超时亏损波动收益",
            description="捕捉因持仓超时导致亏损的模式：当价格波动率上升但收益停滞时，容易触发超时平仓。因子为负波动率调整后的短期收益，高值表示波动大但收益差，预示超时亏损。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol = data['close'].pct_change().rolling(10).std()
        result = (ret - vol * 2).clip(-1, 1)
        return result
