"""AI因子: 缺口回补预警 | 置信:60% | 当开盘价相对于前收盘出现跳空高开（gap_up > 0.5%），且开盘后价格迅速回撤（当前最低价低于开盘价，且回撤幅度超过跳空幅度的一半），则视为假突破，输出负值；若跳空低开后反弹则输出正值；否则输出0。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Gap_Fade_After_Opening(BaseFactor):
    """当开盘价相对于前收盘出现跳空高开（gap_up > 0.5%），且开盘后价格迅速回撤（当前最低价低于开盘价，且回撤幅度超过跳空幅度的一半），则视为假突破，输出负值；若跳空低开后反弹则输出正值；否则输出0。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_gapfade",
            name="Gap Fade After Opening",
            display_name="缺口回补预警",
            description="当开盘价相对于前收盘出现跳空高开（gap_up > 0.5%），且开盘后价格迅速回撤（当前最低价低于开盘价，且回撤幅度超过跳空幅度的一半），则视为假突破，输出负值；若跳空低开后反弹则输出正值；否则输出0。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        open_ = data['open']
        close_prev = data['close'].shift(1)
        high = data['high']
        low = data['low']
        close = data['close']

        # 跳空幅度
        gap = open_ / close_prev - 1.0
        # 高开缺口
        gap_up = gap > 0.005
        # 低开缺口
        gap_down = gap < -0.005

        # 高开后回撤：当前最低价 < 开盘价，且回撤幅度 > 跳空幅度*0.5
        retrace_up = (open_ - low) / open_ > 0.5 * (open_ / close_prev - 1) + 1e-10
        condition_fade_long = gap_up & (low < open_) & retrace_up

        # 低开后反弹：当前最高价 > 开盘价，且反弹幅度 > 向下跳空幅度*0.5
        retrace_down = (high - open_) / open_ > 0.5 * (1 - open_ / close_prev) + 1e-10
        condition_fade_short = gap_down & (high > open_) & retrace_down

        # 因子值：高开假突破给-1，低开假突破给+1，其余0
        result = pd.Series(0.0, index=data.index)
        result[condition_fade_long] = -1.0
        result[condition_fade_short] = 1.0
        return result
