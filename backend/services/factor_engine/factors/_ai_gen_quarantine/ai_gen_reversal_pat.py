"""AI因子: 价格反转模式因子 | 置信:60% | 基于连续多根K线的实体与影线比例，识别潜在的衰竭信号（如长上影线后的小实体）。当出现此类模式时，趋势可能结束，对应未知状态亏损。输出负值表示反转风险高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Price_Reversal_Pattern_Factor(BaseFactor):
    """基于连续多根K线的实体与影线比例，识别潜在的衰竭信号（如长上影线后的小实体）。当出现此类模式时，趋势可能结束，对应未知状态亏损。输出负值表示反转风险高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reversal_pat",
            name="Price Reversal Pattern Factor",
            display_name="价格反转模式因子",
            description="基于连续多根K线的实体与影线比例，识别潜在的衰竭信号（如长上影线后的小实体）。当出现此类模式时，趋势可能结束，对应未知状态亏损。输出负值表示反转风险高。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        open_ = data['open']
        # 计算实体和影线
        body = abs(close - open_)
        upper_shadow = high - np.maximum(close, open_)
        lower_shadow = np.minimum(close, open_) - low
        total_range = high - low
        # 上影线比例（避免除零）
        upper_ratio = upper_shadow / (total_range + 1e-8)
        lower_ratio = lower_shadow / (total_range + 1e-8)
        # 连续两根K线：前一K线上影线长，当前K线实体小（停滞）
        prev_upper = upper_ratio.shift(1)
        prev_body = body.shift(1) / (total_range.shift(1) + 1e-8)
        curr_body = body / (total_range + 1e-8)
        # 信号：prev_upper>0.6 and curr_body<0.3 => 反转风险
        raw = -( (prev_upper > 0.6) & (curr_body < 0.3) ).astype(float)
        # 平滑和归一化到[-1,1]
        result = raw.rolling(3).mean().fillna(0.0)
        return result
