"""AI因子: 未知状态风险逆势 | 置信:50% | 结合亏损中的止损和超时，识别高波动且趋势不明确时的逆势信号，偏向于在极端价格后反向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class UnknownRegimeRiskContrarian(BaseFactor):
    """结合亏损中的止损和超时，识别高波动且趋势不明确时的逆势信号，偏向于在极端价格后反向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unk_risk",
            name="Unknown_Regime_Risk_Contrarian",
            display_name="未知状态风险逆势",
            description="结合亏损中的止损和超时，识别高波动且趋势不明确时的逆势信号，偏向于在极端价格后反向。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(3)
        high_low = (data['high'] - data['low']) / (data['close'] + 1e-9)
        vol = data['close'].pct_change().rolling(5).std()
        result = (-ret * high_low / (vol + 1e-9)).clip(-1, 1)
        return result
