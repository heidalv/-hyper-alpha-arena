"""AI因子: 持仓超时波动惩罚 | 置信:50% | 针对max_hold_timeout亏损模式：当价格在持仓周期内波动收窄且方向不明时，趋势跟踪易超时止损。用长周期收益率与短周期波动率比值，识别低波动横盘状态，值越负表示越可能进入超时陷阱。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Holdtimevolatilitypenalty(BaseFactor):
    """针对max_hold_timeout亏损模式：当价格在持仓周期内波动收窄且方向不明时，趋势跟踪易超时止损。用长周期收益率与短周期波动率比值，识别低波动横盘状态，值越负表示越可能进入超时陷阱。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_holdtime_vol",
            name="HoldTimeVolatilityPenalty",
            display_name="持仓超时波动惩罚",
            description="针对max_hold_timeout亏损模式：当价格在持仓周期内波动收窄且方向不明时，趋势跟踪易超时止损。用长周期收益率与短周期波动率比值，识别低波动横盘状态，值越负表示越可能进入超时陷阱。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret_long = data['close'].pct_change(30)
        vol_short = data['close'].pct_change().rolling(5).std()
        vol_long = data['close'].pct_change().rolling(30).std()
        result = (ret_long / (vol_short + 1e-9)) * (vol_long / (vol_short + 1e-9))
        result = (result / (result.abs().rolling(20).max() + 1e-9)).clip(-1, 1)
        return result.fillna(0)
