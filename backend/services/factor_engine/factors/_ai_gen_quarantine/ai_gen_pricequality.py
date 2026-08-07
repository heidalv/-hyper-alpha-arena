"""AI因子: 价格位置质量 | 置信:55% | 根据K线实体比例和收盘价在区间中的位置评估价格行为的明确性。实体越大、收盘越靠近极端，表明方向越明确（接近+1）；反之小实体、收盘中性表明模糊状态（接近-1），对应regime unknown。使用过去5天平均实体比例与平均位置偏移的复合。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PricePositionQuality(BaseFactor):
    """根据K线实体比例和收盘价在区间中的位置评估价格行为的明确性。实体越大、收盘越靠近极端，表明方向越明确（接近+1）；反之小实体、收盘中性表明模糊状态（接近-1），对应regime unknown。使用过去5天平均实体比例与平均位置偏移的复合。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pricequality",
            name="Price Position Quality",
            display_name="价格位置质量",
            description="根据K线实体比例和收盘价在区间中的位置评估价格行为的明确性。实体越大、收盘越靠近极端，表明方向越明确（接近+1）；反之小实体、收盘中性表明模糊状态（接近-1），对应regime unknown。使用过去5天平均实体比例与平均位置偏移的复合。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        open_ = data['open']
        close = data['close']
        body = (close - open_).abs()
        range_ = high - low
        body_ratio = body / (range_ + 1e-10)  # 实体占比
        # 收盘位置偏移：0~1，取离中间的距离
        pos = (close - low) / (range_ + 1e-10)
        pos_dev = abs(pos - 0.5) * 2  # 0~1
        # 复合：实体大且偏移大则得分高
        score = body_ratio * pos_dev
        avg_score = score.rolling(5).mean()
        # 映射到[-1,1]，0均值附近为0，极端为±1
        result = 2 * avg_score - 1
        result = result.fillna(0.0)
        return result
