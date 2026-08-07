"""AI因子: 假突破检测 | 置信:50% | 检测价格突破布林带上轨但成交量未显著放大或突破后立即回撤的假突破模式。这些场景常导致做多亏损。因子值接近-1表示强烈假突破信号，应避免做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class False_Breakout_Detector(BaseFactor):
    """检测价格突破布林带上轨但成交量未显著放大或突破后立即回撤的假突破模式。这些场景常导致做多亏损。因子值接近-1表示强烈假突破信号，应避免做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pseudobreak",
            name="False_Breakout_Detector",
            display_name="假突破检测",
            description="检测价格突破布林带上轨但成交量未显著放大或突破后立即回撤的假突破模式。这些场景常导致做多亏损。因子值接近-1表示强烈假突破信号，应避免做多。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        volume = data['volume']

        # 布林带参数
        period = 20
        std_mult = 2.0
        ma = close.rolling(period).mean()
        std = close.rolling(period).std()
        upper = ma + std_mult * std
        lower = ma - std_mult * std

        # 突破上轨信号：close > upper，但前一条未突破
        above_upper = close > upper
        breakout = above_upper & (~above_upper.shift(1))

        # 成交量确认：突破时的成交量相对于过去20日均量的比值
        vol_ma = volume.rolling(20).mean()
        vol_ratio = volume / (vol_ma + 1e-10)

        # 回撤检测：突破后2根K线内收盘价低于突破时的高点
        future_return = -(close.shift(-2) - close) / close  # 未来2期负收益即回撤

        # 综合得分：突破但成交量不足(<1.5倍)或回撤
        condition = breakout & ((vol_ratio < 1.5) | (future_return > 0.01))

        # 赋值-1给假突破，0给其他
        result = pd.Series(0.0, index=data.index)
        result[condition] = -1.0
        # 平滑处理：使用expanding或rolling平均使信号连续
        result = result.rolling(5, min_periods=1).mean()
        return result
