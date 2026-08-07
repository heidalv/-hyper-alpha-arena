"""AI因子: 微观趋势衰竭 | 置信:55% | 捕捉价格微小连续上涨后成交量萎缩的做空陷阱。计算过去K线内价格小幅上升次数与成交量递减的相关性，当多头动能衰竭时做空易被止损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Micro Trend Exhaustion(BaseFactor):
    """捕捉价格微小连续上涨后成交量萎缩的做空陷阱。计算过去K线内价格小幅上升次数与成交量递减的相关性，当多头动能衰竭时做空易被止损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_micro_trend",
            name="Micro Trend Exhaustion",
            display_name="微观趋势衰竭",
            description="捕捉价格微小连续上涨后成交量萎缩的做空陷阱。计算过去K线内价格小幅上升次数与成交量递减的相关性，当多头动能衰竭时做空易被止损。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            period = 5
            # 连续上涨次数
            up_count = (data['close'] > data['close'].shift(1)).rolling(period).sum()
            # 成交量递减趋势
            vol_down = (data['volume'] < data['volume'].shift(1)).rolling(period).sum()
            # 微型上涨且量缩
            micro_exhaust = (up_count >= 4) & (vol_down >= 3)
            # 做空风险高，负值信号
            result = -micro_exhaust.astype(float) * 1.0
            return result
