"""AI因子: 超时挤压反转因子 | 置信:55% | 针对多次超时亏损的币种，当价格在布林带内极度挤压后出现假突破，且成交量无法确认趋势时，容易在持仓期内反转。该因子结合价格位置与成交量确认度，识别高概率超时陷阱。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TimeoutSqueezeBreakoutFade(BaseFactor):
    """针对多次超时亏损的币种，当价格在布林带内极度挤压后出现假突破，且成交量无法确认趋势时，容易在持仓期内反转。该因子结合价格位置与成交量确认度，识别高概率超时陷阱。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timeout_squeeze",
            name="Timeout Squeeze Breakout Fade",
            display_name="超时挤压反转因子",
            description="针对多次超时亏损的币种，当价格在布林带内极度挤压后出现假突破，且成交量无法确认趋势时，容易在持仓期内反转。该因子结合价格位置与成交量确认度，识别高概率超时陷阱。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ma20 = data['close'].rolling(20).mean()
        std20 = data['close'].rolling(20).std()
        upper = ma20 + 2 * std20
        lower = ma20 - 2 * std20
        pos = (data['close'] - lower) / (upper - lower + 1e-9)
        squeeze = (std20 / (ma20 + 1e-9)).rolling(10).min()
        vol_chg = data['volume'].pct_change(5)
        result = ((pos - 0.5) * -2 + squeeze * -1 + vol_chg * 0.2).clip(-1, 1)
        return result
