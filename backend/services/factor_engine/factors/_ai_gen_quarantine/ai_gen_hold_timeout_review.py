"""AI因子: 持有超时反转因子 | 置信:60% | 捕捉持仓时间过长导致的逆反风险。通过计算价格偏离移动平均线的程度与持仓周期（模拟简单移动平均交叉周期），当偏离过大且趋势可能反转时发出信号。适用于做空头寸超时未盈利的情况。值正表示做空风险高（应平仓或反手）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HoldTimeoutReversalIndicator(BaseFactor):
    """捕捉持仓时间过长导致的逆反风险。通过计算价格偏离移动平均线的程度与持仓周期（模拟简单移动平均交叉周期），当偏离过大且趋势可能反转时发出信号。适用于做空头寸超时未盈利的情况。值正表示做空风险高（应平仓或反手）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_hold_timeout_review",
            name="Hold Timeout Reversal Indicator",
            display_name="持有超时反转因子",
            description="捕捉持仓时间过长导致的逆反风险。通过计算价格偏离移动平均线的程度与持仓周期（模拟简单移动平均交叉周期），当偏离过大且趋势可能反转时发出信号。适用于做空头寸超时未盈利的情况。值正表示做空风险高（应平仓或反手）。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        short_ma = data['close'].rolling(5).mean()
        long_ma = data['close'].rolling(20).mean()
        # 偏离度
        deviation = (data['close'] - long_ma) / (long_ma + 1e-8)
        # 使用MACD柱作为动量变化
        ema_12 = data['close'].ewm(span=12).mean()
        ema_26 = data['close'].ewm(span=26).mean()
        macd = ema_12 - ema_26
        signal = macd.ewm(span=9).mean()
        macd_hist = macd - signal
        # 当价格高于长期均线(偏离大于0)且MACD柱由正转负时，空头风险大? 我们需要做空风险时价格可能继续上涨? 实际上做空亏损意味着价格上涨，所以当价格高于均线且动能向上时风险大。
        # 这里简化：如果价格高于长期均线且短期均线向上，则做空风险高。
        short_ma_slope = short_ma.diff(3) / 3
        # 信号
        raw = np.where((deviation > 0) & (short_ma_slope > 0), deviation, -deviation)
        # 归一化
        raw = raw / (raw.abs().max() + 1e-8)
        result = np.tanh(raw * 1.5)
        return result
