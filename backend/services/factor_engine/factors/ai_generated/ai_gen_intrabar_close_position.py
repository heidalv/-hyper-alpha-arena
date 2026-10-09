"""AI因子: K线收盘位置强度 | 置信:60% | 收盘价在当日高低区间中的相对位置，衡量买方在当根K线中的掌控力。位置越高说明多头在尾盘占据主导，短期延续上涨概率更高；位置越低则相反。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class IntrabarClosePosition(BaseFactor):
    """收盘价在当日高低区间中的相对位置，衡量买方在当根K线中的掌控力。位置越高说明多头在尾盘占据主导，短期延续上涨概率更高；位置越低则相反。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_intrabar_close_position",
            name="Intrabar Close Position",
            display_name="K线收盘位置强度",
            description="收盘价在当日高低区间中的相对位置，衡量买方在当根K线中的掌控力。位置越高说明多头在尾盘占据主导，短期延续上涨概率更高；位置越低则相反。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        rng = (data['high'] - data['low']) + 1e-9
        pos = (data['close'] - data['low']) / rng
        result = (pos * 2 - 1).rolling(3).mean().clip(-1, 1)
        return result
