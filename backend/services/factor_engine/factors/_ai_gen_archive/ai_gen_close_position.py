"""AI因子: 收盘位置强度 | 置信:57% | 收盘价在当日高低区间中的相对位置反映买卖力量对比：收盘接近最高价说明买方强势，未来延续上涨概率高；收盘接近最低价说明卖方强势。用滚动均值平滑后作为动量确认因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ClosePositionInRange(BaseFactor):
    """收盘价在当日高低区间中的相对位置反映买卖力量对比：收盘接近最高价说明买方强势，未来延续上涨概率高；收盘接近最低价说明卖方强势。用滚动均值平滑后作为动量确认因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_close_position",
            name="Close Position in Range",
            display_name="收盘位置强度",
            description="收盘价在当日高低区间中的相对位置反映买卖力量对比：收盘接近最高价说明买方强势，未来延续上涨概率高；收盘接近最低价说明卖方强势。用滚动均值平滑后作为动量确认因子。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        rng = (data['high'] - data['low']) + 1e-9
        pos = (data['close'] - data['low']) / rng
        result = (pos - 0.5).rolling(5).mean().clip(-1, 1) * 2
        return result
