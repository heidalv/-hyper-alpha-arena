"""AI因子: 未知状态反转 | 置信:60% | 针对regime=unknown场景：在趋势不明时，价格容易在持仓超时后反转。因子结合短期动量与价格位置，当动量减弱且价格偏离移动平均时给出反转信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class UnknownRegimeTrendReversal(BaseFactor):
    """针对regime=unknown场景：在趋势不明时，价格容易在持仓超时后反转。因子结合短期动量与价格位置，当动量减弱且价格偏离移动平均时给出反转信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unknown_regime",
            name="Unknown Regime Trend Reversal",
            display_name="未知状态反转",
            description="针对regime=unknown场景：在趋势不明时，价格容易在持仓超时后反转。因子结合短期动量与价格位置，当动量减弱且价格偏离移动平均时给出反转信号。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ma20 = data['close'].rolling(20).mean()
        ma5 = data['close'].rolling(5).mean()
        mom = data['close'].pct_change(5)
        pos = (data['close'] - ma20) / (data['close'].rolling(20).std() + 1e-9)
        result = (ma5 - ma20) / (ma20 + 1e-9) - 0.5 * mom - 0.3 * pos
        result = result.clip(-1, 1)
        return result
