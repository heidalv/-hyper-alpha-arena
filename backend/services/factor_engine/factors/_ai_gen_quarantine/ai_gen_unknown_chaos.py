"""AI因子: 价格混乱度 | 置信:55% | 利用K线的上下影线长度比例和连续阴阳线切换频率，衡量价格行为的无序程度。当上下影线交替过长、连续阴阳线频繁反转时，市场缺乏明确方向，容易导致策略失效。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Price_Chaos_Index(BaseFactor):
    """利用K线的上下影线长度比例和连续阴阳线切换频率，衡量价格行为的无序程度。当上下影线交替过长、连续阴阳线频繁反转时，市场缺乏明确方向，容易导致策略失效。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unknown_chaos",
            name="Price Chaos Index",
            display_name="价格混乱度",
            description="利用K线的上下影线长度比例和连续阴阳线切换频率，衡量价格行为的无序程度。当上下影线交替过长、连续阴阳线频繁反转时，市场缺乏明确方向，容易导致策略失效。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        open_ = data['open']
        high = data['high']
        low = data['low']
        close = data['close']
        # 上影线比例
        upper_shadow = high - np.maximum(open_, close)
        lower_shadow = np.minimum(open_, close) - low
        body = abs(close - open_)
        total_range = high - low + 1e-10
        upper_ratio = upper_shadow / total_range
        lower_ratio = lower_shadow / total_range
        # 当上下影线都较长且实体较小，表示混乱
        chaos_candle = (upper_ratio > 0.3) & (lower_ratio > 0.3) & (body < total_range * 0.3)
        # 连续阴阳切换：过去3根K线中阴阳交替次数
        direction = np.sign(close - open_)
        switch = (direction.diff() != 0).astype(float)
        switch_3 = switch.rolling(3).sum()
        # 切换频繁表示无序
        high_switch = switch_3 >= 2
        # 综合风险
        risk = (chaos_candle.astype(float) + high_switch.astype(float)) / 2
        risk = risk.clip(0, 1) * 2 - 1
        return risk.fillna(-1)
