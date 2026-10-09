"""AI因子: 利润回撤保护因子 | 置信:60% | 针对profit_drawdown_full亏损，识别价格冲高后快速回落且伴随放量的形态，用于提前规避利润回撤。用短期动量反转和成交量放大程度构建。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Profitdrawdownguard(BaseFactor):
    """针对profit_drawdown_full亏损，识别价格冲高后快速回落且伴随放量的形态，用于提前规避利润回撤。用短期动量反转和成交量放大程度构建。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_profit_dd_guard",
            name="ProfitDrawdownGuard",
            display_name="利润回撤保护因子",
            description="针对profit_drawdown_full亏损，识别价格冲高后快速回落且伴随放量的形态，用于提前规避利润回撤。用短期动量反转和成交量放大程度构建。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        ret_short = close.pct_change(3)
        ret_long = close.pct_change(10)
        vol_spike = volume / (volume.rolling(20).mean() + 1e-9)
        result = ((ret_short - ret_long) * (vol_spike - 1)).clip(-1, 1)
        return result
