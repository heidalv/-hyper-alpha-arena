"""AI因子: 止损趋势弱势因子 | 置信:58% | 亏损中sl（止损）频繁出现且regime=unknown，说明在无明确趋势时做反向交易易触发止损。该因子检测短期动量与长期均线的关系：当短期动量弱于长期趋势且价格低于关键均线时，提示趋势不明，给予负向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Stoplosstrendweakness(BaseFactor):
    """亏损中sl（止损）频繁出现且regime=unknown，说明在无明确趋势时做反向交易易触发止损。该因子检测短期动量与长期均线的关系：当短期动量弱于长期趋势且价格低于关键均线时，提示趋势不明，给予负向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sl_trend",
            name="StopLossTrendWeakness",
            display_name="止损趋势弱势因子",
            description="亏损中sl（止损）频繁出现且regime=unknown，说明在无明确趋势时做反向交易易触发止损。该因子检测短期动量与长期均线的关系：当短期动量弱于长期趋势且价格低于关键均线时，提示趋势不明，给予负向信号。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_ma = data['close'].rolling(5).mean()
        long_ma = data['close'].rolling(20).mean()
        momentum = data['close'].pct_change(3)
        result = -((short_ma < long_ma).astype(float) * (momentum < 0).astype(float))
        return result.clip(-1, 1)
