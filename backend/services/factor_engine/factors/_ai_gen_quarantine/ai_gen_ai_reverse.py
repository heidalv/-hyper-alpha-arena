"""AI因子: AI模式反转 | 置信:50% | 利用短期趋势加速度和波动率变化识别潜在的AI反转信号。计算过去N根K线的动量斜率变化，若斜率由正转负且波动率上升，则预测向下反转（空头信号-1）；反之向上反转（+1）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class AIPatternReversal(BaseFactor):
    """利用短期趋势加速度和波动率变化识别潜在的AI反转信号。计算过去N根K线的动量斜率变化，若斜率由正转负且波动率上升，则预测向下反转（空头信号-1）；反之向上反转（+1）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ai_reverse",
            name="AI Pattern Reversal",
            display_name="AI模式反转",
            description="利用短期趋势加速度和波动率变化识别潜在的AI反转信号。计算过去N根K线的动量斜率变化，若斜率由正转负且波动率上升，则预测向下反转（空头信号-1）；反之向上反转（+1）。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 参数
        short_lookback = 5
        long_lookback = 20
        # 计算短期和长期动量
        close = data['close']
        short_ma = close.rolling(window=short_lookback).mean()
        long_ma = close.rolling(window=long_lookback).mean()
        # 动量差（短期-长期）
        momentum_diff = short_ma - long_ma
        # 动量变化率（加速度）
        momentum_accel = momentum_diff.diff()
        # 波动率（ATR简化版）
        high = data['high']
        low = data['low']
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(window=long_lookback).mean()
        # 波动率变化
        atr_change = atr.pct_change()
        # 信号：当动量差 > 0 但加速度 < 0 且波动率上升时，看跌；反之看涨
        bearish = (momentum_diff > 0) & (momentum_accel < 0) & (atr_change > 0.01)
        bullish = (momentum_diff < 0) & (momentum_accel > 0) & (atr_change > 0.01)
        factor = pd.Series(0, index=data.index)
        factor[bearish] = -1
        factor[bullish] = 1
        return factor
