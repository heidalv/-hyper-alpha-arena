"""AI因子: 持仓超时波动惩罚 | 置信:70% | 亏损集中在max_hold_timeout，表明持仓时间过长时趋势衰竭。用20日收益率绝对值与波动率比值，在低波动时给予负向惩罚，避免长期持仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Holdtimevolatilitypenalty(BaseFactor):
    """亏损集中在max_hold_timeout，表明持仓时间过长时趋势衰竭。用20日收益率绝对值与波动率比值，在低波动时给予负向惩罚，避免长期持仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_holdtime_vol",
            name="HoldTimeVolatilityPenalty",
            display_name="持仓超时波动惩罚",
            description="亏损集中在max_hold_timeout，表明持仓时间过长时趋势衰竭。用20日收益率绝对值与波动率比值，在低波动时给予负向惩罚，避免长期持仓。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        result = (ret / (vol + 1e-9)) * -1
        result = result.clip(-1, 1)
        return result
