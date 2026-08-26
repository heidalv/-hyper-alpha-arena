"""AI因子: 放量反转确认 | 置信:65% | 亏损中SL触发较多，且regime=unknown，表明逆势入场。用短期价格反转与放量结合，当价格下跌但成交量放大时给出负向信号，避免逆势做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volumereversalconfirmation(BaseFactor):
    """亏损中SL触发较多，且regime=unknown，表明逆势入场。用短期价格反转与放量结合，当价格下跌但成交量放大时给出负向信号，避免逆势做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reversal_volume",
            name="VolumeReversalConfirmation",
            display_name="放量反转确认",
            description="亏损中SL触发较多，且regime=unknown，表明逆势入场。用短期价格反转与放量结合，当价格下跌但成交量放大时给出负向信号，避免逆势做多。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret1 = data['close'].pct_change(1)
        vol_ratio = data['volume'] / data['volume'].rolling(20).mean()
        result = (ret1 * -1) * (vol_ratio - 1)
        result = result.clip(-1, 1)
        return result
