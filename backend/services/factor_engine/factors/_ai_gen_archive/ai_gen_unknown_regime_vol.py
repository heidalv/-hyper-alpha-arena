"""AI因子: 未知市场状态波动惩罚 | 置信:60% | 所有亏损均发生在regime=unknown状态，表明市场状态不明确时信号可靠性差。该因子识别高波动且方向不明确的市场（价格在均线附近震荡），此时应降低交易倾向，因子值趋近0；当趋势明确时因子值接近+1或-1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class UnknownRegimeVolatility(BaseFactor):
    """所有亏损均发生在regime=unknown状态，表明市场状态不明确时信号可靠性差。该因子识别高波动且方向不明确的市场（价格在均线附近震荡），此时应降低交易倾向，因子值趋近0；当趋势明确时因子值接近+1或-1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unknown_regime_vol",
            name="Unknown_Regime_Volatility",
            display_name="未知市场状态波动惩罚",
            description="所有亏损均发生在regime=unknown状态，表明市场状态不明确时信号可靠性差。该因子识别高波动且方向不明确的市场（价格在均线附近震荡），此时应降低交易倾向，因子值趋近0；当趋势明确时因子值接近+1或-1。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ma = data['close'].rolling(20).mean()
        std = data['close'].rolling(20).std()
        dist = (data['close'] - ma).abs() / (std + 1e-9)
        vol_ratio = data['close'].pct_change().rolling(10).std() / (data['close'].pct_change().rolling(30).std() + 1e-9)
        result = (dist - vol_ratio * 0.5).clip(-1, 1)
        return result
