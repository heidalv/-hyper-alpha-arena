"""AI因子: 收盘位置反转 | 置信:58% | 微观结构代理：收盘价在当日高低区间中的相对位置。收盘位置极高（接近最高价）后短期易回落，极低（接近最低价）后短期易反弹，取负号捕捉反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ClosePositionReversal(BaseFactor):
    """微观结构代理：收盘价在当日高低区间中的相对位置。收盘位置极高（接近最高价）后短期易回落，极低（接近最低价）后短期易反弹，取负号捕捉反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_close_pos_rev",
            name="Close Position Reversal",
            display_name="收盘位置反转",
            description="微观结构代理：收盘价在当日高低区间中的相对位置。收盘位置极高（接近最高价）后短期易回落，极低（接近最低价）后短期易反弹，取负号捕捉反转。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        rng = data['high'] - data['low'] + 1e-9
        pos = (data['close'] - data['low']) / rng
        result = (-(pos - 0.5) * 2).rolling(3).mean().clip(-1, 1)
        return result
