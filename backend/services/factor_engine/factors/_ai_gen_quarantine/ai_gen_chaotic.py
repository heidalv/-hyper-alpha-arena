"""AI因子: 方向混沌指数 | 置信:60% | 衡量短期价格方向变化的频繁程度，即市场是否处于无序震荡状态。计算方法：统计最近N根K线中相邻两根收盘价方向变化的次数，除以总可能变化次数。值接近0表示趋势一致，接近1表示极度震荡。然后映射到[-1,1]，负值表示趋势，正值表示混沌。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Directional Chaos Index(BaseFactor):
    """衡量短期价格方向变化的频繁程度，即市场是否处于无序震荡状态。计算方法：统计最近N根K线中相邻两根收盘价方向变化的次数，除以总可能变化次数。值接近0表示趋势一致，接近1表示极度震荡。然后映射到[-1,1]，负值表示趋势，正值表示混沌。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_chaotic",
            name="Directional Chaos Index",
            display_name="方向混沌指数",
            description="衡量短期价格方向变化的频繁程度，即市场是否处于无序震荡状态。计算方法：统计最近N根K线中相邻两根收盘价方向变化的次数，除以总可能变化次数。值接近0表示趋势一致，接近1表示极度震荡。然后映射到[-1,1]，负值表示趋势，正值表示混沌。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            n = 14
            close = data['close']
            direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
            # 方向变化标志 (相邻不同)
            change = (direction.shift() != direction).astype(int)
            # 滚动求和
            chaos = change.rolling(n, min_periods=n).sum() / (n - 1)
            # 映射到[-1,1]: 混沌 -> 正, 趋势 -> 负
            result = 2 * chaos - 1
            return result.fillna(0)
