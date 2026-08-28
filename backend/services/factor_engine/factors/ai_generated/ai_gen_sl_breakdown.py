"""AI因子: 止损击穿动量反转 | 置信:50% | 多次止损亏损发生在趋势末端，价格快速反向突破。该因子捕捉短期急跌（负收益率）伴随放量，且价格低于近期均线，预示继续下探风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class StopLossBreakdownMomentum(BaseFactor):
    """多次止损亏损发生在趋势末端，价格快速反向突破。该因子捕捉短期急跌（负收益率）伴随放量，且价格低于近期均线，预示继续下探风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sl_breakdown",
            name="stop_loss_breakdown_momentum",
            display_name="止损击穿动量反转",
            description="多次止损亏损发生在趋势末端，价格快速反向突破。该因子捕捉短期急跌（负收益率）伴随放量，且价格低于近期均线，预示继续下探风险。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret1 = data['close'].pct_change(1)
        ret5 = data['close'].pct_change(5)
        vol_ratio = data['volume'] / (data['volume'].rolling(20).mean() + 1e-9)
        below_ma = (data['close'] < data['close'].rolling(20).mean()).astype(float)
        result = (-ret1 * vol_ratio * below_ma - ret5 * 0.5).clip(-1, 1)
        return result
